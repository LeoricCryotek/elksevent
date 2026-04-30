# -*- coding: utf-8 -*-
"""Event booking fields on project.task.

Each event booking is a project task inside the current Elk Year's
"Event Bookings" project.  This inheritance layer adds:
  - Event details (date, time, type, guest count)
  - Board approval workflow with secretary notification
  - Room / venue selection with per-booking rates
  - Event cost lines (setup, event, cleanup hours, supplies, bar, coordinator)
  - Coordinator fee — configurable as fixed or % of room income
  - Customer invoicing — single-line or itemised by room
  - Budget control — costs cannot exceed billed without override
  - Elk Year project auto-creation (April – March)
"""
import logging
from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


EVENT_TYPE_CHOICES = [
    ('wedding', 'Wedding / Reception'),
    ('birthday', 'Birthday Party'),
    ('anniversary', 'Anniversary'),
    ('corporate', 'Corporate Event'),
    ('fundraiser', 'Fundraiser'),
    ('memorial', 'Memorial / Celebration of Life'),
    ('meeting', 'Meeting / Conference'),
    ('holiday', 'Holiday Party'),
    ('other', 'Other'),
]

BOARD_STATUS_CHOICES = [
    ('not_submitted', 'Not Yet Submitted'),
    ('submitted', 'Submitted to Board'),
    ('approved', 'Approved'),
    ('denied', 'Denied'),
]


class ProjectTask(models.Model):
    _inherit = 'project.task'

    # ------------------------------------------------------------------
    # Flag: is this task an event booking?
    # ------------------------------------------------------------------
    x_is_event = fields.Boolean(
        "Is Event Booking",
        compute='_compute_is_event', store=True,
        help="True when this task belongs to an Event Bookings project.",
    )

    # ------------------------------------------------------------------
    # Event details
    # ------------------------------------------------------------------
    x_event_type = fields.Selection(
        EVENT_TYPE_CHOICES, string="Event Type", tracking=True,
    )
    x_event_date = fields.Date("Event Date", tracking=True)
    x_event_start_time = fields.Float(
        "Start Time", tracking=True,
        help="Event start time (24h decimal, e.g. 14.5 = 2:30 PM).",
    )
    x_event_end_time = fields.Float(
        "End Time", tracking=True,
        help="Event end time (24h decimal, e.g. 22.0 = 10:00 PM).",
    )
    x_event_duration = fields.Float(
        "Duration (hrs)", compute='_compute_event_duration', store=True,
    )
    x_guest_count = fields.Integer("Expected Guests", tracking=True)
    x_event_description = fields.Text(
        "Event Description",
        help="Customer's description of what they're planning.",
    )
    x_special_requests = fields.Text("Special Requests")

    # Customer contact (separate from partner_id for public submissions)
    x_customer_name = fields.Char("Customer Name", tracking=True)
    x_customer_phone = fields.Char("Customer Phone")
    x_customer_email = fields.Char("Customer Email")

    # ------------------------------------------------------------------
    # Board approval — full audit trail
    # ------------------------------------------------------------------
    x_board_status = fields.Selection(
        BOARD_STATUS_CHOICES, string="Board Status",
        default='not_submitted', tracking=True, copy=False,
    )
    x_board_submitted_date = fields.Date(
        "Submitted to Board", tracking=True, copy=False,
    )
    x_board_meeting_date = fields.Date(
        "Board Meeting Date", tracking=True, copy=False,
        help="Date of the board meeting where this event will be reviewed.",
    )
    x_board_approval_date = fields.Date(
        "Approval / Denial Date", tracking=True, copy=False,
    )
    x_board_approved_by = fields.Many2one(
        'res.users', string="Approved / Denied By",
        tracking=True, copy=False,
    )
    x_board_notes = fields.Text(
        "Board Notes", copy=False,
        help="Any notes or conditions from the board vote.",
    )
    x_board_denial_reason = fields.Text("Denial Reason", copy=False)

    # ------------------------------------------------------------------
    # Deposit
    # ------------------------------------------------------------------
    x_deposit_amount = fields.Monetary(
        "Deposit Amount", tracking=True, currency_field='x_currency_id',
    )
    x_deposit_received = fields.Boolean("Deposit Received", tracking=True)
    x_deposit_date = fields.Date("Deposit Date", tracking=True)
    x_deposit_method = fields.Selection([
        ('cash', 'Cash'),
        ('check', 'Check'),
        ('card', 'Credit/Debit Card'),
        ('other', 'Other'),
    ], string="Deposit Method")
    x_deposit_reference = fields.Char(
        "Deposit Reference",
        help="Check number, transaction ID, etc.",
    )

    # ------------------------------------------------------------------
    # Room bookings
    # ------------------------------------------------------------------
    x_room_booking_ids = fields.One2many(
        'elks.event.room.booking', 'event_id', string="Rooms Booked",
    )
    x_room_income = fields.Monetary(
        "Room Income", currency_field='x_currency_id',
        compute='_compute_financials', store=True,
        help="Total of all room booking subtotals (rate + cleaning + service).",
    )

    # ------------------------------------------------------------------
    # Cost lines (the 'PO section')
    # ------------------------------------------------------------------
    x_cost_line_ids = fields.One2many(
        'elks.event.cost.line', 'event_id', string="Event Costs",
    )
    x_total_costs = fields.Monetary(
        "Total Costs", currency_field='x_currency_id',
        compute='_compute_financials', store=True,
    )

    # ------------------------------------------------------------------
    # Coordinator fee
    # ------------------------------------------------------------------
    x_currency_id = fields.Many2one(
        'res.currency', string="Currency",
        default=lambda self: self.env.company.currency_id,
    )
    x_coordinator_fee_pct = fields.Float(
        "Coordinator Fee %", default=20.0,
        help="Percentage of room income for event coordinator compensation.",
    )
    x_coordinator_fee = fields.Monetary(
        "Coordinator Fee", currency_field='x_currency_id',
        compute='_compute_financials', store=True,
    )

    # ------------------------------------------------------------------
    # Legacy simple financial fields (kept for backward compat, but room
    # bookings and cost lines are the primary source now)
    # ------------------------------------------------------------------
    x_room_rental_rate = fields.Monetary(
        "Room Rental Rate", currency_field='x_currency_id', tracking=True,
    )
    x_staff_count = fields.Integer("Staff Needed", tracking=True)
    x_staff_hourly_rate = fields.Monetary(
        "Staff Hourly Rate", currency_field='x_currency_id',
    )
    x_staff_hours_before = fields.Float("Setup Hours (before event)")
    x_staff_hours_during = fields.Float("Hours During Event")
    x_staff_hours_after = fields.Float("Cleanup Hours (after event)")
    x_staff_total_hours = fields.Float(
        "Total Staff Hours",
        compute='_compute_staff_total', store=True,
    )
    x_staff_total_cost = fields.Monetary(
        "Total Staff Cost", currency_field='x_currency_id',
        compute='_compute_staff_total', store=True,
    )
    x_linen_charges = fields.Monetary(
        "Linen Charges", currency_field='x_currency_id', tracking=True,
    )
    x_supply_charges = fields.Monetary(
        "Supply Charges", currency_field='x_currency_id', tracking=True,
    )
    x_additional_charges = fields.Monetary(
        "Additional Charges", currency_field='x_currency_id', tracking=True,
    )
    x_additional_charges_note = fields.Char("Additional Charges Note")

    # ------------------------------------------------------------------
    # Invoice & billing
    # ------------------------------------------------------------------
    x_invoice_style = fields.Selection([
        ('single', 'Single Line (Total)'),
        ('itemized', 'Itemized by Room'),
    ], string="Invoice Style", default='single',
        help="Single Line: one line with the total amount and contract note. "
             "Itemized: one line per room (rate + cleaning + service combined).",
    )
    x_contract_notes = fields.Text(
        "Contract / Agreement Notes",
        help="Included on the invoice when using single-line billing.",
    )
    x_invoice_ids = fields.One2many(
        'account.move', 'x_event_id', string="Invoices",
    )
    x_invoice_count = fields.Integer(
        compute='_compute_invoice_count', string="Invoice Count",
    )
    x_total_billed = fields.Monetary(
        "Total Billed", currency_field='x_currency_id',
        compute='_compute_financials', store=True,
        help="Room income + coordinator fee (what the customer is charged).",
    )
    x_balance_due = fields.Monetary(
        "Balance Due", currency_field='x_currency_id',
        compute='_compute_financials', store=True,
    )

    # Legacy totals (for backward compat)
    x_subtotal = fields.Monetary(
        "Subtotal", currency_field='x_currency_id',
        compute='_compute_financials', store=True,
    )
    x_event_total = fields.Monetary(
        "Event Total", currency_field='x_currency_id',
        compute='_compute_financials', store=True,
    )

    # ------------------------------------------------------------------
    # Linked POs
    # ------------------------------------------------------------------
    x_purchase_order_ids = fields.One2many(
        'purchase.order', 'x_event_id', string="Purchase Orders",
    )
    x_po_count = fields.Integer(
        compute='_compute_po_count', string="PO Count",
    )
    x_po_total = fields.Monetary(
        "PO Total", currency_field='x_currency_id',
        compute='_compute_po_count',
    )

    # Budget override
    x_budget_remaining = fields.Monetary(
        "Budget Remaining", currency_field='x_currency_id',
        compute='_compute_financials', store=True,
    )

    # ------------------------------------------------------------------
    # Computed: is_event
    # ------------------------------------------------------------------
    @api.depends('project_id')
    def _compute_is_event(self):
        """True if task belongs to any event bookings project (including
        Elk Year projects that are children of the base project)."""
        base_project = self.env.ref(
            'elksevent.project_event_bookings', raise_if_not_found=False,
        )
        event_project_ids = set()
        if base_project:
            event_project_ids.add(base_project.id)
        # Also find Elk Year projects by naming convention
        year_projects = self.env['project.project'].sudo().search([
            ('name', 'like', 'Events %-%'),
        ])
        event_project_ids.update(year_projects.ids)
        for rec in self:
            rec.x_is_event = rec.project_id.id in event_project_ids

    # ------------------------------------------------------------------
    # Computed: event duration
    # ------------------------------------------------------------------
    @api.depends('x_event_start_time', 'x_event_end_time')
    def _compute_event_duration(self):
        for rec in self:
            if rec.x_event_start_time and rec.x_event_end_time:
                dur = rec.x_event_end_time - rec.x_event_start_time
                rec.x_event_duration = max(dur, 0.0)
            else:
                rec.x_event_duration = 0.0

    # ------------------------------------------------------------------
    # Computed: staff totals (legacy fields)
    # ------------------------------------------------------------------
    @api.depends(
        'x_staff_count', 'x_staff_hourly_rate',
        'x_staff_hours_before', 'x_staff_hours_during', 'x_staff_hours_after',
    )
    def _compute_staff_total(self):
        for rec in self:
            total_hrs = (
                (rec.x_staff_hours_before or 0.0)
                + (rec.x_staff_hours_during or 0.0)
                + (rec.x_staff_hours_after or 0.0)
            )
            rec.x_staff_total_hours = total_hrs * (rec.x_staff_count or 0)
            rec.x_staff_total_cost = (
                rec.x_staff_total_hours * (rec.x_staff_hourly_rate or 0.0)
            )

    # ------------------------------------------------------------------
    # Computed: financials (room income, coordinator fee, totals)
    # ------------------------------------------------------------------
    @api.depends(
        'x_room_booking_ids.subtotal',
        'x_cost_line_ids.total',
        'x_coordinator_fee_pct',
        'x_room_rental_rate',
        'x_staff_total_cost',
        'x_linen_charges', 'x_supply_charges', 'x_additional_charges',
        'x_deposit_amount', 'x_deposit_received',
        'x_purchase_order_ids.amount_total',
        'x_purchase_order_ids.state',
    )
    def _compute_financials(self):
        Settings = self.env['elks.lodge.settings'].sudo()
        settings = Settings.search([], limit=1)
        for rec in self:
            # Room income from room booking lines
            room_income = sum(rec.x_room_booking_ids.mapped('subtotal'))
            # Fall back to legacy field if no room bookings
            if not room_income and rec.x_room_rental_rate:
                room_income = rec.x_room_rental_rate
            rec.x_room_income = room_income

            # Coordinator fee
            if settings and settings.x_coordinator_fee_type == 'fixed':
                rec.x_coordinator_fee = settings.x_coordinator_fixed_rate or 0.0
            else:
                pct = rec.x_coordinator_fee_pct or 0.0
                if settings and settings.x_coordinator_percentage:
                    pct = settings.x_coordinator_percentage
                rec.x_coordinator_fee = room_income * (pct / 100.0)

            # Total billed = room income + coordinator fee
            rec.x_total_billed = room_income + rec.x_coordinator_fee
            rec.x_subtotal = rec.x_total_billed
            rec.x_event_total = rec.x_total_billed

            # Total costs from cost lines
            rec.x_total_costs = sum(rec.x_cost_line_ids.mapped('total'))

            # Balance due
            deposit = rec.x_deposit_amount if rec.x_deposit_received else 0.0
            rec.x_balance_due = rec.x_total_billed - deposit

            # Budget remaining (billed minus costs AND linked POs)
            po_total = sum(
                rec.x_purchase_order_ids.filtered(
                    lambda p: p.state != 'cancel'
                ).mapped('amount_total')
            )
            rec.x_budget_remaining = rec.x_total_billed - rec.x_total_costs - po_total

    # ------------------------------------------------------------------
    # Computed: invoice count
    # ------------------------------------------------------------------
    @api.depends('x_invoice_ids')
    def _compute_invoice_count(self):
        for rec in self:
            rec.x_invoice_count = len(rec.x_invoice_ids)

    # ------------------------------------------------------------------
    # Computed: PO count & total
    # ------------------------------------------------------------------
    @api.depends('x_purchase_order_ids')
    def _compute_po_count(self):
        for rec in self:
            pos = rec.x_purchase_order_ids.filtered(
                lambda p: p.state != 'cancel'
            )
            rec.x_po_count = len(pos)
            rec.x_po_total = sum(pos.mapped('amount_total'))

    # ------------------------------------------------------------------
    # Board approval actions
    # ------------------------------------------------------------------
    def action_submit_to_board(self):
        """Mark event as submitted for board approval and notify secretary."""
        self.ensure_one()
        if not self.x_event_date:
            raise UserError(_(
                "Please set the event date before submitting to the board."
            ))
        vals = {
            'x_board_status': 'submitted',
            'x_board_submitted_date': fields.Date.context_today(self),
        }
        # Move to the "Submitted to Board" stage
        submitted_stage = self.env.ref(
            'elksevent.stage_submitted_to_board', raise_if_not_found=False,
        )
        if submitted_stage:
            vals['stage_id'] = submitted_stage.id
        self.write(vals)
        self.message_post(
            body=_(
                "<b>Submitted to Board</b> for approval by %s.",
                self.env.user.name,
            ),
            subtype_xmlid='mail.mt_comment',
        )
        # Notify secretary group via activity
        self._notify_secretary_board_submission()

    def _notify_secretary_board_submission(self):
        """Schedule an activity for the secretary group about this event."""
        secretary_group = self.env.ref(
            'elkssecretary.group_elkssecretary_secretary',
            raise_if_not_found=False,
        )
        if not secretary_group:
            return
        # Find a secretary user to assign the activity
        secretary_user = secretary_group.users[:1]
        if not secretary_user:
            return
        self.activity_schedule(
            'mail.mail_activity_data_todo',
            user_id=secretary_user.id,
            summary=_(
                "Event submitted for Board agenda: %s", self.name,
            ),
            note=_(
                "Event '%(name)s' on %(date)s for %(guests)d guests has been "
                "submitted for board approval. Please add to the next board "
                "meeting agenda.\n\nCustomer: %(customer)s",
                name=self.name,
                date=self.x_event_date,
                guests=self.x_guest_count or 0,
                customer=self.x_customer_name or 'N/A',
            ),
        )

    def action_board_approve(self):
        """Mark event as approved by the board and move to Booked."""
        self.ensure_one()
        vals = {
            'x_board_status': 'approved',
            'x_board_approval_date': fields.Date.context_today(self),
            'x_board_approved_by': self.env.uid,
        }
        booked_stage = self.env.ref(
            'elksevent.stage_booked', raise_if_not_found=False,
        )
        if booked_stage:
            vals['stage_id'] = booked_stage.id
        self.write(vals)
        self.message_post(
            body=_(
                "<b>Board Approved</b> on %(date)s by %(user)s.%(notes)s",
                date=self.x_board_approval_date,
                user=self.env.user.name,
                notes=(
                    f"<br/>Notes: {self.x_board_notes}"
                    if self.x_board_notes else ""
                ),
            ),
            subtype_xmlid='mail.mt_note',
        )

    def action_board_deny(self):
        """Mark event as denied by the board and move to Cancelled."""
        self.ensure_one()
        vals = {
            'x_board_status': 'denied',
            'x_board_approval_date': fields.Date.context_today(self),
            'x_board_approved_by': self.env.uid,
        }
        cancelled_stage = self.env.ref(
            'elksevent.stage_cancelled', raise_if_not_found=False,
        )
        if cancelled_stage:
            vals['stage_id'] = cancelled_stage.id
        self.write(vals)
        self.message_post(
            body=_(
                "<b>Board Denied</b> on %(date)s by %(user)s.%(reason)s",
                date=self.x_board_approval_date,
                user=self.env.user.name,
                reason=(
                    f"<br/>Reason: {self.x_board_denial_reason}"
                    if self.x_board_denial_reason else ""
                ),
            ),
            subtype_xmlid='mail.mt_note',
        )

    def action_receive_deposit(self):
        """Record deposit as received."""
        self.ensure_one()
        if not self.x_deposit_amount:
            raise UserError(_("Please enter a deposit amount first."))
        self.write({
            'x_deposit_received': True,
            'x_deposit_date': fields.Date.context_today(self),
        })
        self.message_post(
            body=_(
                "<b>Deposit Received</b>: $%(amount).2f on %(date)s.",
                amount=self.x_deposit_amount,
                date=self.x_deposit_date,
            ),
            subtype_xmlid='mail.mt_note',
        )

    # ------------------------------------------------------------------
    # Invoice generation
    # ------------------------------------------------------------------
    def action_create_invoice(self):
        """Create a customer invoice from this event's billing."""
        self.ensure_one()
        if not self.x_total_billed or self.x_total_billed <= 0:
            raise UserError(_(
                "There is nothing to invoice. Add room bookings first."
            ))

        partner = self._get_event_partner()
        move_vals = {
            'move_type': 'out_invoice',
            'partner_id': partner.id,
            'x_event_id': self.id,
            'narration': self.x_contract_notes or '',
            'invoice_origin': f"Event: {self.name}",
        }

        lines = []
        if self.x_invoice_style == 'itemized' and self.x_room_booking_ids:
            # One line per room
            for booking in self.x_room_booking_ids:
                lines.append((0, 0, {
                    'name': f"Room: {booking.room_id.name}",
                    'quantity': 1,
                    'price_unit': booking.subtotal,
                }))
            # Coordinator fee as separate line
            if self.x_coordinator_fee:
                lines.append((0, 0, {
                    'name': "Event Coordinator Fee",
                    'quantity': 1,
                    'price_unit': self.x_coordinator_fee,
                }))
        else:
            # Single line with total
            desc = f"Event: {self.name}"
            if self.x_event_date:
                desc += f" ({self.x_event_date})"
            if self.x_contract_notes:
                desc += f"\n{self.x_contract_notes}"
            lines.append((0, 0, {
                'name': desc,
                'quantity': 1,
                'price_unit': self.x_total_billed,
            }))

        move_vals['invoice_line_ids'] = lines
        invoice = self.env['account.move'].create(move_vals)

        self.message_post(
            body=_(
                "<b>Invoice Created</b>: "
                "<a href='#' data-oe-model='account.move' "
                "data-oe-id='%(inv_id)s'>%(inv_name)s</a> "
                "for $%(amount).2f",
                inv_id=invoice.id,
                inv_name=invoice.name,
                amount=self.x_total_billed,
            ),
            subtype_xmlid='mail.mt_note',
        )

        return {
            'type': 'ir.actions.act_window',
            'name': _("Event Invoice"),
            'res_model': 'account.move',
            'res_id': invoice.id,
            'view_mode': 'form',
        }

    def _get_event_partner(self):
        """Get or create a partner for the event customer."""
        if self.partner_id:
            return self.partner_id
        # Search by customer name/email
        Partner = self.env['res.partner']
        if self.x_customer_email:
            partner = Partner.search([
                ('email', '=', self.x_customer_email),
            ], limit=1)
            if partner:
                return partner
        if self.x_customer_name:
            partner = Partner.search([
                ('name', '=', self.x_customer_name),
            ], limit=1)
            if partner:
                return partner
            # Create new partner
            return Partner.create({
                'name': self.x_customer_name,
                'email': self.x_customer_email or False,
                'phone': self.x_customer_phone or False,
                'customer_rank': 1,
            })
        raise UserError(_(
            "Please enter a customer name or select a partner before "
            "creating an invoice."
        ))

    # ------------------------------------------------------------------
    # Smart buttons
    # ------------------------------------------------------------------
    def action_view_invoices(self):
        """Smart button: show invoices linked to this event."""
        self.ensure_one()
        action = {
            'type': 'ir.actions.act_window',
            'name': _("Event Invoices"),
            'res_model': 'account.move',
            'domain': [('x_event_id', '=', self.id)],
            'view_mode': 'list,form',
        }
        if self.x_invoice_count == 1:
            action['res_id'] = self.x_invoice_ids[0].id
            action['view_mode'] = 'form'
        return action

    def action_view_purchase_orders(self):
        """Smart button: show POs linked to this event."""
        self.ensure_one()
        action = {
            'type': 'ir.actions.act_window',
            'name': _("Event Purchase Orders"),
            'res_model': 'purchase.order',
            'domain': [('x_event_id', '=', self.id)],
            'view_mode': 'list,form',
        }
        if self.x_po_count == 1:
            action['res_id'] = self.x_purchase_order_ids[0].id
            action['view_mode'] = 'form'
        return action

    # ------------------------------------------------------------------
    # Elk Year project auto-creation
    # ------------------------------------------------------------------
    @api.model
    def _cron_create_elk_year_project(self):
        """Create an Elk Year event project if one does not exist yet.

        Elk Year runs April 1 – March 31.  On April 1 the cron creates
        'Events 2026-2027'.  Stages from the base project are reused.
        """
        today = fields.Date.context_today(self)
        # Only run on April 1 (or first cron run after)
        if today.month < 4:
            year_label = f"{today.year - 1}-{today.year}"
        else:
            year_label = f"{today.year}-{today.year + 1}"

        project_name = f"Events {year_label}"
        existing = self.env['project.project'].search([
            ('name', '=', project_name),
        ], limit=1)
        if existing:
            return  # Already created

        base_project = self.env.ref(
            'elksevent.project_event_bookings', raise_if_not_found=False,
        )
        # Get stage IDs from base project (or from our data records)
        stage_ids = []
        for xmlid in [
            'elksevent.stage_new', 'elksevent.stage_contacted',
            'elksevent.stage_submitted_to_board', 'elksevent.stage_booked',
            'elksevent.stage_after_action', 'elksevent.stage_completed',
            'elksevent.stage_cancelled',
        ]:
            stage = self.env.ref(xmlid, raise_if_not_found=False)
            if stage:
                stage_ids.append(stage.id)

        project = self.env['project.project'].create({
            'name': project_name,
            'description': f"Event bookings for Elk Year {year_label}.",
            'privacy_visibility': 'portal',
            'allow_task_dependencies': True,
        })
        # Link stages to new project
        for stage_id in stage_ids:
            stage = self.env['project.task.type'].browse(stage_id)
            stage.write({'project_ids': [(4, project.id)]})

        _logger.info("Created Elk Year event project: %s", project_name)
