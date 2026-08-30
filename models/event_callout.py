# -*- coding: utf-8 -*-
"""Digital department call-out for an event (Bar / Kitchen / Custodial).

HUMAN
-----
When the coordinator needs a department to staff an event, they "send" a
call-out to that department's manager. The manager gets an email + a card in
their website portal (/my) where they review the estimated staff counts and
hours, adjust them, add notes, and CERTIFY — confirming the resources they
will commit. On certification the numbers flow back into the event estimate
(so the quote / P&L reflect them) and the printed call-out PDF is attached to
the event for the After Action.

AI
--
- Model `elks.event.callout`; one per (event_id, department). department in
  bar/kitchen/custodial. state draft -> sent -> certified.
- FIELD_MAP maps this record's generic staffing fields onto the department's
  specific project.task fields; _seed_from_event() copies task->callout and
  _push_to_event() writes callout->task (sudo).
- Portal access via a record rule (manager_partner_id == user.partner_id);
  the controller does sensitive writes (push, attach, notify) with sudo.
"""
import base64

from odoo import _, api, fields, models
from odoo.exceptions import UserError

CALLOUT_DEPARTMENTS = [
    ('bar', 'Bar'),
    ('kitchen', 'Kitchen'),
    ('custodial', 'Custodial'),
]

# department -> {callout field: task field}. Only listed fields are synced.
FIELD_MAP = {
    'bar': {
        'num_bars': 'x_num_bars',
        'primary_count': 'x_bartender_count',
        'primary_hours': 'x_plan_bar_hours',
        'drink_requests': 'x_bar_drink_requests',
    },
    'kitchen': {
        'primary_count': 'x_cook_count',
        'primary_hours': 'x_cook_hours',
        'secondary_count': 'x_kitchen_support_count',
        'secondary_hours': 'x_kitchen_support_hours',
    },
    'custodial': {
        'primary_count': 'x_cleaner_count',
        'primary_hours': 'x_cleaner_hours',
        'ready_datetime': 'x_cleanup_datetime',
    },
}


class EventCallout(models.Model):
    _name = "elks.event.callout"
    _description = "Event Department Call-Out"
    _order = "create_date desc, id desc"

    event_id = fields.Many2one(
        'project.task', string="Event", required=True, ondelete='cascade',
        index=True)
    department = fields.Selection(
        CALLOUT_DEPARTMENTS, string="Department", required=True, index=True)
    manager_partner_id = fields.Many2one(
        'res.partner', string="Department Manager", index=True,
        help="The manager who reviews and certifies this call-out in their "
             "portal.")
    state = fields.Selection([
        ('draft', 'Draft'),
        ('sent', 'Sent to Manager'),
        ('certified', 'Certified'),
    ], default='draft', required=True, index=True, tracking=False)

    # Generic staffing estimate (mapped per department via FIELD_MAP)
    num_bars = fields.Integer("Number of Bars")
    primary_count = fields.Integer("Staff Count")
    primary_hours = fields.Float("Hours Each")
    secondary_count = fields.Integer("Support Staff Count")
    secondary_hours = fields.Float("Support Hours Each")
    ready_datetime = fields.Datetime("Building Ready By")
    drink_requests = fields.Text("Drink Requests")
    manager_notes = fields.Text(
        "Manager Notes",
        help="Anything else pertinent — availability, constraints, extra "
             "supplies, sign-off comments.")
    customer_request = fields.Text(
        "Customer Requests", readonly=True,
        help="What the customer requested for this department (read-only). "
             "Seeded from the event; the manager sets the actual staffing.")

    certified_by = fields.Many2one('res.users', string="Certified By",
                                   copy=False)
    certified_on = fields.Datetime("Certified On", copy=False)

    # Named staff roster (who works, hours, cost) + the department gratuity.
    line_ids = fields.One2many(
        'elks.event.callout.line', 'callout_id', string="Staff Roster")
    gratuity = fields.Monetary(
        "Gratuity (total)", currency_field='currency_id',
        help="Total gratuity for this department's staff. On certification it "
             "feeds the event's gratuity pool (distributed to staff by hours).")
    staff_count = fields.Integer(
        "People", compute='_compute_roster_totals', store=False)
    hours_total = fields.Float(
        "Total Hours", compute='_compute_roster_totals', store=False)
    cost_total = fields.Monetary(
        "Total Labor Cost", currency_field='currency_id',
        compute='_compute_roster_totals', store=False)

    # Convenience mirrors for the portal templates
    event_name = fields.Char(related='event_id.name', string="Event")
    event_date = fields.Date(related='event_id.x_event_date', string="Date")
    department_label = fields.Char(
        compute='_compute_department_label', string="Department Name")
    currency_id = fields.Many2one(related='event_id.x_currency_id')

    @api.depends('line_ids.hours', 'line_ids.cost')
    def _compute_roster_totals(self):
        for rec in self:
            rec.staff_count = len(rec.line_ids)
            rec.hours_total = sum(rec.line_ids.mapped('hours'))
            rec.cost_total = sum(rec.line_ids.mapped('cost'))

    @api.depends('department')
    def _compute_department_label(self):
        labels = dict(CALLOUT_DEPARTMENTS)
        for rec in self:
            rec.department_label = labels.get(rec.department, '')

    # ── Sync helpers ───────────────────────────────────────────────────
    def _seed_from_event(self):
        """Copy the event's current estimate into this call-out."""
        for rec in self:
            evt = rec.event_id
            fmap = FIELD_MAP.get(rec.department, {})
            vals = {co_f: evt[task_f] for co_f, task_f in fmap.items()}
            # One-way: the customer's request note is read-only for the manager.
            cr_field = rec._DEPT_CUSTOMER_REQUEST_FIELD.get(rec.department)
            if cr_field:
                vals['customer_request'] = evt[cr_field] or ''
            if vals:
                rec.write(vals)

    def _push_to_event(self):
        """Write the certified estimate back onto the event (sudo caller)."""
        for rec in self:
            fmap = FIELD_MAP.get(rec.department, {})
            vals = {task_f: rec[co_f] for co_f, task_f in fmap.items()}
            if vals:
                rec.event_id.write(vals)

    def _report_xmlid(self):
        self.ensure_one()
        return 'elksevent.report_event_callout_%s' % self.department

    # ── Certification (called from the portal controller) ──────────────
    def action_certify(self):
        """Manager certifies: lock the numbers onto the event, attach the
        printed call-out to the event, and notify the coordinator."""
        for rec in self:
            if rec.state == 'certified':
                continue
            rec.write({
                'state': 'certified',
                'certified_by': rec.env.uid,
                'certified_on': fields.Datetime.now(),
            })
            # Note: the requested counts on the event are the customer's ask and
            # are left untouched; the manager's actual staffing lives in the
            # roster (line_ids) synced below. So we no longer _push_to_event().
            rec.sudo()._sync_roster_and_gratuity()
            # Reflect the certified roster labor in the event's cost lines.
            rec.event_id.sudo()._sync_labor_cost_lines()
            rec.sudo()._attach_certified_pdf()
            rec.sudo()._notify_coordinator_certified()
        return True

    # department -> event gratuity field / planned-roster role
    _DEPT_GRATUITY_FIELD = {'bar': 'x_bar_gratuity',
                            'kitchen': 'x_kitchen_gratuity'}
    _DEPT_ROLE = {'bar': 'bartender', 'kitchen': 'cook',
                  'custodial': 'cleaning'}
    # department -> event field holding the customer's request note
    _DEPT_CUSTOMER_REQUEST_FIELD = {
        'bar': 'x_bar_customer_request',
        'kitchen': 'x_kitchen_customer_request',
        'custodial': 'x_custodial_customer_request',
    }

    def _sync_roster_and_gratuity(self):
        """On certify, push the department gratuity into the event's pool and
        add the named EMPLOYEES to the event's planned staff roster (so their
        clock-in auto-links). Free-text names stay on the call-out only."""
        self.ensure_one()
        evt = self.event_id
        grat_field = self._DEPT_GRATUITY_FIELD.get(self.department)
        if grat_field and self.gratuity:
            evt[grat_field] = self.gratuity
        role = self._DEPT_ROLE.get(self.department)
        if role:
            Assign = self.env['elks.event.staff.assignment']
            have = set(evt.x_staff_assignment_ids.filtered(
                lambda a: a.role == role).mapped('employee_id').ids)
            for line in self.line_ids:
                emp = line.employee_id
                if emp and emp.id not in have:
                    Assign.create({
                        'event_id': evt.id,
                        'employee_id': emp.id,
                        'role': role,
                    })
                    have.add(emp.id)

    def _attach_certified_pdf(self):
        self.ensure_one()
        try:
            pdf, _dummy = self.env['ir.actions.report']._render_qweb_pdf(
                self._report_xmlid(), self.event_id.ids)
            fname = '%s Call-Out (Certified) - %s.pdf' % (
                self.department_label, self.event_id.name or 'Event')
            att = self.env['ir.attachment'].create({
                'name': fname,
                'type': 'binary',
                'datas': base64.b64encode(pdf),
                'res_model': 'project.task',
                'res_id': self.event_id.id,
                'mimetype': 'application/pdf',
            })
            self.event_id.message_post(
                body=_("%(dept)s call-out certified by %(mgr)s.",
                       dept=self.department_label,
                       mgr=self.manager_partner_id.name or self.env.user.name),
                attachment_ids=[att.id],
                subtype_xmlid='mail.mt_note')
        except Exception:  # noqa: BLE001 - never block certification
            pass

    def _notify_coordinator_certified(self):
        self.ensure_one()
        try:
            user = self.event_id._after_action_user()
            self.event_id.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=_("%(dept)s call-out certified: %(evt)s",
                          dept=self.department_label,
                          evt=self.event_id.name or 'Event'),
                note=_("The %(dept)s manager certified their staffing for this "
                       "event. Review the updated estimate.",
                       dept=self.department_label),
                user_id=user.id)
            # Also email the coordinator directly.
            if user.email:
                base = self.env['ir.config_parameter'].sudo().get_param(
                    'web.base.url') or ''
                link = '%s/web#id=%s&model=project.task&view_type=form' % (
                    base, self.event_id.id)
                self.env['mail.mail'].sudo().create({
                    'subject': _('%(dept)s call-out certified - %(evt)s',
                                 dept=self.department_label,
                                 evt=self.event_id.name or 'Event'),
                    'body_html': _(
                        '<p>Hello %(name)s,</p><p>The %(dept)s manager '
                        '(%(mgr)s) certified their staffing for '
                        '<strong>%(evt)s</strong>: %(people)s people, '
                        '%(hours)g hours, $%(cost).2f labor'
                        '%(grat)s.</p><p><a href="%(link)s">Open the '
                        'event</a></p>',
                        name=user.name,
                        dept=self.department_label,
                        mgr=self.manager_partner_id.name or 'the manager',
                        evt=self.event_id.name or 'the event',
                        people=self.staff_count,
                        hours=self.hours_total,
                        cost=self.cost_total,
                        grat=(_(', $%.2f gratuity') % self.gratuity)
                        if self.gratuity else '',
                        link=link),
                    'email_to': user.email,
                }).send()
        except Exception:  # noqa: BLE001
            pass

    def _send_reminder(self):
        """Bump the manager: re-email the portal link for a call-out still
        awaiting certification. Best-effort; no-op once certified."""
        self.ensure_one()
        if self.state != 'sent':
            return False
        mgr = self.manager_partner_id
        base = self.env['ir.config_parameter'].sudo().get_param(
            'web.base.url') or ''
        link = '%s/my/callout/%s' % (base, self.id)
        if mgr and mgr.email:
            self.env['mail.mail'].sudo().create({
                'subject': _('Reminder: %(dept)s call-out needs your sign-off '
                             '- %(name)s',
                             dept=self.department_label,
                             name=self.event_id.name or 'Event'),
                'body_html': _(
                    '<p>Hello %(mgr)s,</p><p>Reminder: the %(dept)s call-out '
                    'for <strong>%(name)s</strong> still needs your review and '
                    'certification.</p><p><a href="%(link)s">Open the '
                    'call-out</a></p>',
                    mgr=mgr.name, dept=self.department_label,
                    name=self.event_id.name or 'the event', link=link),
                'email_to': mgr.email,
            }).send()
        self.event_id.message_post(
            body=_("Reminder sent to %(mgr)s for the %(dept)s call-out.",
                   mgr=(mgr.name or 'the manager'),
                   dept=self.department_label),
            subtype_xmlid='mail.mt_note')
        return True

    @api.model
    def _cron_remind_pending(self):
        """Daily bump for call-outs still awaiting certification, limited to
        events that have not yet happened."""
        today = fields.Date.context_today(self)
        for co in self.search([('state', '=', 'sent')]):
            evt_date = co.event_id.x_event_date
            if not evt_date or evt_date >= today:
                co._send_reminder()
        return True

    @api.model
    def _get_or_create(self, event, department):
        """Return the single call-out for (event, department), creating and
        seeding it from the event on first use."""
        rec = self.search([
            ('event_id', '=', event.id),
            ('department', '=', department),
        ], limit=1)
        if not rec:
            rec = self.create({
                'event_id': event.id,
                'department': department,
                'manager_partner_id': event._callout_manager(department).id,
            })
            rec._seed_from_event()
        return rec


class EventCalloutLine(models.Model):
    _name = "elks.event.callout.line"
    _description = "Event Call-Out Staff Line"
    _order = "id"

    callout_id = fields.Many2one(
        'elks.event.callout', string="Call-Out", required=True,
        ondelete='cascade', index=True)
    employee_id = fields.Many2one(
        'hr.employee', string="Employee",
        help="Pick a lodge employee (links to payroll / attendance), or leave "
             "blank and type a name for outside help.")
    person_name = fields.Char(
        "Name", help="Free-text name when the person isn't a lodge employee.")
    role = fields.Char("Role / Shift")
    start_time = fields.Float(
        "Start", help="Shift start (24h, e.g. 15.5 = 3:30 PM).")
    end_time = fields.Float("End", help="Shift end (24h).")
    hours = fields.Float(
        "Hours",
        help="Shift length. Auto-filled from start/end when both are set, "
             "otherwise entered directly.")
    rate = fields.Monetary(
        "Rate / hr", currency_field='currency_id',
        help="Pay rate per hour for this person.")
    cost = fields.Monetary(
        "Cost", currency_field='currency_id',
        compute='_compute_cost', store=True, readonly=True,
        help="Hours x rate.")
    currency_id = fields.Many2one(related='callout_id.currency_id')

    @api.depends('hours', 'rate')
    def _compute_cost(self):
        for rec in self:
            rec.cost = (rec.hours or 0.0) * (rec.rate or 0.0)
    display_name = fields.Char(compute='_compute_display_name')
    start_str = fields.Char(compute='_compute_time_str')
    end_str = fields.Char(compute='_compute_time_str')

    @api.depends('employee_id', 'person_name')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = (
                rec.employee_id.name or rec.person_name or _("(unnamed)"))

    @api.depends('start_time', 'end_time')
    def _compute_time_str(self):
        """HH:MM strings for the <input type='time'> fields on the portal."""
        for rec in self:
            rec.start_str = self._float_to_hhmm(rec.start_time)
            rec.end_str = self._float_to_hhmm(rec.end_time)

    @staticmethod
    def _float_to_hhmm(value):
        if not value:
            return ''
        hh = int(value)
        mm = int(round((value - hh) * 60))
        if mm == 60:
            hh += 1
            mm = 0
        return '%02d:%02d' % (hh % 24, mm)
