# -*- coding: utf-8 -*-
"""Event booking fields and workflow on project.task.

HUMAN
-----
This is the heart of the module. An "event" is just a Project task living in
the current Elk-Year "Events" project, with a lot bolted on so the lodge can
run a facility rental end to end:
  - the event details and the staff "checklist" (tables, bar, linens, …);
  - a Board -> Floor approval workflow (only the right people can approve);
  - rooms, an itemized staff-only Quote, and a guest-driven cost build;
  - two customer invoices (deposit + final) and the coordinator fee;
  - a tentative calendar hold that turns solid on approval;
  - customer emails, a tax/UBI flag for the assessor, and a double-booking guard.
Most buttons on the event form call a method in this file.

AI
--
- Inherits `project.task`; all custom fields/methods are x_*-prefixed.
- `x_is_event` (computed, stored) gates everything to event tasks.
- Approval: x_approval_state draft->board->floor->approved|rejected, mirroring
  elkspurchase; actions are group-gated via _ensure_approver + has_group.
- Money: legacy room/cost fields still compute, but x_total_billed/x_balance_due
  and the P&L now PREFER x_sale_order_id (the quote), net of member discount.
- Side-effect hooks fire on floor approval: checklist subtasks, calendar
  promotion, approval email. Quote build/invoice/calendar/email helpers are all
  in their own SECTION banners below. Settings read via _event_settings().
- Elk-Year project auto-creation (April–March) drives x_is_event + cron.
"""
import base64
import logging
import math
import re
from datetime import date, timedelta

import pytz
from lxml import etree
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

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

# The event categories offered by the K&K / Gallagher Affinity "Elks Lodge
# Facility Rental Insurance" application. Our own event types are mapped onto
# these so staff can complete the K&K form; the category is editable per event.
INSURANCE_EVENT_CATEGORIES = [
    ('baptism', 'Baptism, Christening, Communion, Confirmation'),
    ('dinner', 'Dinner, Luncheon, Picnic'),
    ('family', 'Family Event, Graduation, Birthday, Anniversary or Celebration'),
    ('fundraiser', 'Fundraiser / Benefit'),
    ('memorial', 'Funeral or Memorial Service'),
    ('reunion', 'Reunion (class, family, military)'),
    ('wedding', 'Wedding Ceremony, Reception and Rehearsal Dinner'),
]

# Our event type -> K&K category default (staff can override on the event).
EVENT_TYPE_TO_INSURANCE = {
    'wedding': 'wedding',
    'birthday': 'family',
    'anniversary': 'family',
    'holiday': 'family',
    'fundraiser': 'fundraiser',
    'memorial': 'memorial',
    'corporate': 'dinner',
    'meeting': 'dinner',
    # 'other' -> left blank so staff choose the right K&K category.
}

# Activities K&K deems INELIGIBLE — no facility-rental coverage is provided.
# Shown as prohibited on the public form and printed on the worksheet.
PROHIBITED_ACTIVITIES = [
    "An Elks Function or Elks-Sponsored Activity",
    "Events that include motorized vehicles / motorcycles / watercraft",
    "Fireworks",
    "Gun and/or knife shows",
    "Inflatable amusement devices/rides (bounce houses, slides, climbing towers)",
    "Mechanical amusement rides",
    "Organized athletic events and competitions (does not apply to billiards, "
    "darts, shuffleboard, bean bag toss/cornhole)",
]

EVENT_APPROVAL_STATES = [
    ('draft', 'Draft'),
    ('board', 'Board Review'),
    ('floor', 'Floor Vote'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
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
    # Event type flags (drive billing + tax reporting)
    # ------------------------------------------------------------------
    x_is_elks_event = fields.Boolean(
        "Elks Event", tracking=True, copy=False,
        help="Lodge's own event. Runs the same checklist/approval/calendar "
             "pipeline but is NOT billed — no quote, invoices, or deposit holds.",
    )
    x_is_member = fields.Boolean(
        "Member Rental", tracking=True,
        help="The renter is an Elks member (gets the member discount). "
             "Member rentals are not unrelated business income (UBI).",
    )
    x_is_nonprofit = fields.Boolean(
        "Non-Profit", tracking=True,
        help="The renter is a non-profit organization. Non-profit and member "
             "rentals get an automatic 50% discount on the room rate.",
    )
    x_member_number = fields.Char(
        "Member Number", copy=False,
        help="Elks membership number — shown on the quote and invoice.",
    )
    # ──────────────────────────────────────────────────────────────────
    # SECTION: Manual discount (fields)
    # HUMAN: Staff choose when/why a discount applies (no automatic member
    #        discount). Pick a reason, then give it as either a percent or a
    #        fixed dollar amount off the ROOM RENTAL only — event costs and the
    #        coordinator fee are never discounted. A written note is required.
    # AI: _event_room_discount() returns (rooms_net, discount) applied to the
    #     room only; _check_discount_note enforces the note. member/nonprofit
    #     default to 50% via _onchange_discount_reason.
    # ──────────────────────────────────────────────────────────────────
    x_discount_reason = fields.Selection([
        ('member', 'Member'),
        ('nonprofit', 'Non-Profit'),
        ('officer_board', 'Officer / Board Approved'),
        ('other', 'Other'),
    ], string="Discount Reason", tracking=True,
        help="Why a discount is applied. Leave blank for no discount.")
    x_discount_type = fields.Selection([
        ('percent', 'Percent (%)'),
        ('amount', 'Amount ($)'),
    ], string="Discount Type", default='percent', tracking=True,
        help="Give the discount as a percent of the discountable subtotal, "
             "or as a fixed dollar amount.")
    x_discount_pct = fields.Float(
        "Discount %", tracking=True,
        help="Percent off the ROOM RENTAL only (event costs are never "
             "discounted). Applied when Discount Type is Percent and a Reason "
             "is set.")
    x_discount_value = fields.Monetary(
        "Discount Amount", currency_field='x_currency_id', tracking=True,
        help="Fixed dollar discount off the ROOM RENTAL only (event costs and "
             "the coordinator fee are never discounted). Applied when Discount "
             "Type is Amount and a Reason is set.")
    x_discount_note = fields.Char(
        "Discount Note", tracking=True,
        help="Required whenever a discount is applied — explain why.")
    # Elks (lodge's own) events — who owns it; not billed, no insurance needed.
    x_event_committee = fields.Char(
        "Committee",
        help="The lodge committee responsible for this Elks event.")
    x_responsible_party_id = fields.Many2one(
        'res.partner', string="Responsible Party",
        help="The person responsible for this Elks event.")
    x_is_ubi = fields.Boolean(
        "Taxable (UBI)", compute='_compute_is_ubi', store=True,
        help="Unrelated Business Income: a non-member, non-Elks event with "
             "net income. Appears on the Assessor / UBI report.",
    )
    x_event_net_income = fields.Monetary(
        "Net Income", currency_field='x_currency_id',
        compute='_compute_is_ubi', store=True,
        help="Total billed minus total costs.",
    )

    # Promotional links (entered on the web form / event)
    x_facebook_event_url = fields.Char(
        "Facebook Event URL",
        help="Link to the Facebook event page, if any.",
    )
    x_related_url = fields.Char(
        "Related Website URL",
        help="Any other website URL related to this event.",
    )
    x_double_booking_override = fields.Boolean(
        "Double-Booking Override", copy=False,
        help="Allow this event to share a room/date/time with another event. "
             "Restricted to managers.",
    )

    # ------------------------------------------------------------------
    # Event details
    # ------------------------------------------------------------------
    x_event_type = fields.Selection(
        EVENT_TYPE_CHOICES, string="Event Type", tracking=True,
    )
    x_event_date = fields.Date("Event Date", tracking=True)
    x_event_start_time = fields.Float(
        "Start Time (24h)", tracking=True,
        help="Event start time (24h decimal, e.g. 14.5 = 2:30 PM). "
             "Usually set via the text field, not edited directly.",
    )
    x_event_end_time = fields.Float(
        "End Time (24h)", tracking=True,
        help="Event end time (24h decimal, e.g. 22.0 = 10:00 PM). "
             "Usually set via the text field, not edited directly.",
    )
    # Text mirrors of the start/end times. The Float fields above store the
    # real value (decimal hours); these Char fields let humans type "10:30 am"
    # and are what the WEBSITE FORM BUILDER should bind to (a Float renders as
    # a number box on the website, which rejects "10:30 am").
    x_event_start_time_text = fields.Char(
        "Start Time", compute='_compute_event_time_text',
        inverse='_inverse_event_time_text', store=True,
        help="Type a time like '10:30 am' or '2 pm'.",
    )
    x_event_end_time_text = fields.Char(
        "End Time", compute='_compute_event_time_text',
        inverse='_inverse_event_time_text', store=True,
        help="Type a time like '12:00 pm' or '11 pm'.",
    )
    # Selectable (dropdown) mirrors in 15-minute steps for the BACKEND form.
    # Keys are minutes-since-midnight; the canonical value still lives in the
    # Float fields (which feed the calendar), so picking here updates them and
    # the calendar booking follows. See _event_time_options / _float_to_minkey.
    x_event_start_sel = fields.Selection(
        selection='_event_time_options', string="Start Time",
        compute='_compute_event_time_sel',
        inverse='_inverse_event_time_sel', store=True,
    )
    x_event_end_sel = fields.Selection(
        selection='_event_time_options', string="End Time",
        compute='_compute_event_time_sel',
        inverse='_inverse_event_time_sel', store=True,
    )
    x_requested_entry = fields.Datetime(
        "Requested Entry",
        help="Day-of-event entry: the date & time the host requests to enter "
             "on the day of the event.")
    x_requested_setup = fields.Datetime(
        "Requested Setup",
        help="Date & time the host requests to begin setup (may be before the "
             "event day).")
    x_access_window_ids = fields.One2many(
        'elks.event.access.window', 'event_id',
        string="Additional Access Windows",
        help="Extra access / setup times beyond entry & setup, each with the "
             "staff assigned to be there.")
    x_cleanup_scheduled = fields.Boolean(
        "Cleanup Scheduled",
        help="Tick to schedule a cleanup date & time for this event.")
    x_cleanup_datetime = fields.Datetime(
        "Cleanup Date & Time",
        help="When cleanup is scheduled for this event.")
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
    # Label "Host Name" (not "Customer Name") to avoid clashing with the
    # partner_name field website_project adds to project.task.
    x_customer_name = fields.Char("Host Name", tracking=True)
    x_customer_phone = fields.Char("Host Phone")
    x_customer_email = fields.Char("Host Email")

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Celebration-of-Life (memorial) — honoree Elk details
    # HUMAN: When the event is a Celebration of Life / memorial we ask
    #        whether the person being honored was an Elk and, if so, their
    #        member number and home lodge (for records + any member courtesy).
    # AI: Only meaningful when x_event_type == 'memorial'; shown conditionally
    #     on the backend + both public forms.
    # ──────────────────────────────────────────────────────────────────
    x_col_is_elk = fields.Boolean(
        "Honoree was an Elk",
        help="Celebration of Life: was the person being honored an Elk member?")
    x_col_member_number = fields.Char(
        "Honoree Member #",
        help="Elk membership number of the person being honored.")
    x_col_home_lodge = fields.Char(
        "Honoree Home Lodge",
        help="Home lodge name/number of the person being honored.")

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Facility-rental insurance (K&K / Gallagher Affinity)
    # HUMAN: Every non-lodge event needs K&K rental insurance. We map the
    #        event to K&K's category, confirm none of their prohibited
    #        activities apply, and (on approval) get a to-do with the link.
    # AI: x_insurance_event_category defaults from x_event_type via
    #     EVENT_TYPE_TO_INSURANCE (editable). x_prohibited_ack must be true
    #     to be eligible. The insurance to-do fires on Board approval
    #     (_finalize_event_approval).
    # ──────────────────────────────────────────────────────────────────
    x_insurance_event_category = fields.Selection(
        INSURANCE_EVENT_CATEGORIES, string="Insurance Event Category",
        compute='_compute_insurance_event_category',
        store=True, readonly=False,
        help="K&K facility-rental insurance category. Defaults from the event "
             "type; adjust if K&K classifies it differently.")
    x_prohibited_ack = fields.Boolean(
        "No Prohibited Activities",
        help="Confirms NONE of the K&K-ineligible activities (fireworks, "
             "motorized vehicles, inflatables, gun/knife shows, organized "
             "athletics, etc.) are part of this event. Required for coverage.")

    @api.depends('x_event_type')
    def _compute_insurance_event_category(self):
        for rec in self:
            mapped = EVENT_TYPE_TO_INSURANCE.get(rec.x_event_type or '')
            # Default from the mapping; for an unmapped type ('other') keep any
            # existing manual pick. Always assign so the stored compute is happy.
            if mapped:
                rec.x_insurance_event_category = mapped
            else:
                rec.x_insurance_event_category = (
                    rec.x_insurance_event_category or False)

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Department managers + digital call-outs
    # HUMAN: Each event routes its Bar/Kitchen/Custodial call-out to a
    #        manager. The manager defaults from Lodge Settings but can be
    #        changed per event. Sending a call-out gives that manager a
    #        portal item to review, adjust and certify (see elks.event.callout).
    # AI: Per-event manager fields default from settings via compute
    #     (readonly=False). x_callout_ids is the o2m of sent call-outs.
    # ──────────────────────────────────────────────────────────────────
    x_bar_manager_id = fields.Many2one(
        'res.partner', string="Bar Manager (event)",
        compute='_compute_event_dept_managers', store=True, readonly=False)
    x_kitchen_manager_id = fields.Many2one(
        'res.partner', string="Kitchen Manager (event)",
        compute='_compute_event_dept_managers', store=True, readonly=False)
    x_custodial_manager_id = fields.Many2one(
        'res.partner', string="Custodial Manager (event)",
        compute='_compute_event_dept_managers', store=True, readonly=False)
    x_callout_ids = fields.One2many(
        'elks.event.callout', 'event_id', string="Department Call-Outs")
    x_bar_callout_certified = fields.Boolean(
        compute='_compute_callout_certified', string="Bar Call-Out Certified")
    x_kitchen_callout_certified = fields.Boolean(
        compute='_compute_callout_certified',
        string="Kitchen Call-Out Certified")
    x_custodial_callout_certified = fields.Boolean(
        compute='_compute_callout_certified',
        string="Custodial Call-Out Certified")

    @api.depends('x_callout_ids.state', 'x_callout_ids.department')
    def _compute_callout_certified(self):
        for rec in self:
            done = {c.department for c in rec.x_callout_ids
                    if c.state == 'certified'}
            rec.x_bar_callout_certified = 'bar' in done
            rec.x_kitchen_callout_certified = 'kitchen' in done
            rec.x_custodial_callout_certified = 'custodial' in done

    @api.depends('x_is_event')
    def _compute_event_dept_managers(self):
        """Default each event's department manager from the employee who holds
        that Event Department Role; a coordinator can override per event."""
        Emp = self.env['hr.employee']
        m_bar = Emp._event_dept_manager_partner('bar')
        m_kit = Emp._event_dept_manager_partner('kitchen')
        m_cus = Emp._event_dept_manager_partner('custodial')
        for rec in self:
            rec.x_bar_manager_id = rec.x_bar_manager_id or m_bar
            rec.x_kitchen_manager_id = rec.x_kitchen_manager_id or m_kit
            rec.x_custodial_manager_id = rec.x_custodial_manager_id or m_cus

    def _callout_manager(self, dept):
        """Manager partner for a department: the per-event override field, else
        the employee holding that Event Department Role."""
        self.ensure_one()
        field = {'bar': 'x_bar_manager_id',
                 'kitchen': 'x_kitchen_manager_id',
                 'custodial': 'x_custodial_manager_id'}[dept]
        partner = self[field]
        if not partner:
            partner = self.env['hr.employee']._event_dept_manager_partner(dept)
        return partner or self.env['res.partner']

    def _callout_for(self, dept):
        """The department call-out record for this event (for the PDF roster)."""
        self.ensure_one()
        return self.env['elks.event.callout'].sudo().search([
            ('event_id', '=', self.id), ('department', '=', dept),
        ], limit=1)

    # department -> the event flag that means "this department is in use"
    _DEPT_USED_FIELD = {'bar': 'x_bar_use',
                        'kitchen': 'x_food_request',
                        'custodial': 'x_cleaning_use'}

    def _kickoff_department_callouts(self):
        """Send each in-use department's digital call-out to its manager — the
        checklist kickoff. Everything entered on the event (counts, hours,
        drink/customer requests, gratuity) is carried on the call-out that is
        emailed. Idempotent: skips call-outs already sent/certified, and skips
        departments with no manager set (nothing to email)."""
        self.ensure_one()
        Callout = self.env['elks.event.callout']
        for dept, used_field in self._DEPT_USED_FIELD.items():
            if not self[used_field]:
                continue
            existing = Callout.sudo().search([
                ('event_id', '=', self.id), ('department', '=', dept),
            ], limit=1)
            if existing and existing.state in ('sent', 'certified'):
                continue
            if not self._callout_manager(dept):
                continue
            try:
                self._send_digital_callout(dept)
            except Exception:  # noqa: BLE001 - never block the kickoff
                pass

    # ------------------------------------------------------------------
    # Event Checklist — the 20-point worksheet answers (drive subtasks)
    # ------------------------------------------------------------------
    x_need_tables = fields.Integer("Tables Needed")
    x_need_chairs = fields.Integer("Chairs Needed")
    x_need_podium = fields.Boolean("Podium")
    x_need_sound = fields.Boolean("Sound System")
    x_need_microphone = fields.Boolean("Microphone")
    x_need_projector = fields.Boolean("Projector")
    x_need_linen = fields.Boolean("Linen")
    x_linen_qty = fields.Integer("Linen Qty")
    x_linen_color = fields.Char("Linen Color")
    x_need_insurance = fields.Boolean("Insurance Required")
    x_insurance_on_file = fields.Boolean("Insurance Certificate on File")
    # Insurance is mandatory for every event. Either the renter provides their
    # own certificate (naming the lodge as additional insured) OR the lodge
    # provides it and adds it to the quote.
    x_insurance_by = fields.Selection([
        ('renter', 'Renter provides own (lodge named as additional insured)'),
        ('lodge', 'Lodge provides (added to the quote)'),
    ], string="Insurance Provided By", default='lodge')
    x_use_kitchen = fields.Boolean("Use of Kitchen")
    x_need_garbage = fields.Boolean(
        "Garbage Handling", default=True,
        help="The lodge handles garbage for every event.")
    x_bar_use = fields.Boolean(
        "Bar Use",
        help="Include the bar for this event — auto-adds the Bar Service cost "
             "(bartender labor + bar fee).")
    x_num_bars = fields.Integer("Number of Bars")
    x_bartender_count = fields.Integer(
        "Bartenders", compute='_compute_bartenders',
        store=True, readonly=False,
        help="Auto: one bartender per N guests (N = 'Guests per Bartender' in "
             "Settings, e.g. 75). Editable — change it and it sticks until the "
             "guest count changes.")
    x_need_beer_tub = fields.Boolean("Beer Tub")
    x_bar_drink_requests = fields.Text(
        "Drink Requests",
        help="Specific drinks / bar requests for the bartender (signature "
             "cocktails, wine, kegs, etc.). Printed on the Bar call-out.")
    # Customer requests / thoughts, captured per department. These are what the
    # CUSTOMER asked for (not a directive to staff); they print on that
    # department's call sheet under "Requested by the customer" so the manager
    # decides the actual staffing.
    x_bar_customer_request = fields.Text(
        "Bar - Customer Requests",
        help="What the customer requested for the bar (their thoughts / asks). "
             "Prints on the Bar call sheet as requested by the customer.")
    x_kitchen_customer_request = fields.Text(
        "Kitchen - Customer Requests",
        help="What the customer requested for food / catering. Prints on the "
             "Kitchen call sheet as requested by the customer.")
    x_custodial_customer_request = fields.Text(
        "Cleaning - Customer Requests",
        help="What the customer requested for cleaning / setup. Prints on the "
             "Custodial call sheet as requested by the customer.")
    # Event workers — mirrors the bar: a checkbox to include event-staff labor,
    # a worker count and hours each, priced at the event-staff rate + event
    # service fee (Lodge Settings). Drives the Event Service cost line.
    x_event_workers_use = fields.Boolean(
        "Event Workers",
        help="Include event workers for this event — auto-adds the Event "
             "Service cost (worker labor + event service fee).")
    x_event_worker_count = fields.Integer(
        "Event Workers", help="Number of event-staff workers.")
    x_event_worker_hours = fields.Float(
        "Hours per Worker", help="Planned hours each event worker works.")
    # Cleaning crew — mirrors event workers: a checkbox to include cleaning
    # labor, a cleaner count and hours each, priced at the custodial rate +
    # custodial service fee (Lodge Settings). Drives the Cleaning Service line.
    x_cleaning_use = fields.Boolean(
        "Cleaning",
        help="Include cleaning for this event — auto-adds the Cleaning "
             "Service cost (custodial labor + custodial service fee).")
    x_cleaner_count = fields.Integer(
        "Cleaners", help="Number of cleaning / custodial staff.")
    x_cleaner_hours = fields.Float(
        "Hours per Cleaner", help="Planned hours each cleaner works.")
    x_custodial_ready_datetime = fields.Datetime(
        "Building Clean & Restocked By",
        help="Deadline (typically the day after the event) for custodial to "
             "have the building fully cleaned and the bathrooms restocked. "
             "Printed on the custodial call-out.")
    x_has_decorations = fields.Boolean("Decorations")
    x_decoration_notes = fields.Text("Decoration Notes")
    _US_THEM = [('us', 'Us (Lodge)'), ('them', 'Them (Renter)')]
    x_decor_takedown_by = fields.Selection(
        _US_THEM, string="Decoration Takedown By")
    x_setup_by = fields.Selection(_US_THEM, string="Setup By")
    x_takedown_by = fields.Selection(_US_THEM, string="Takedown By")
    x_marketing_signage = fields.Boolean(
        "Marketing Signage Use",
        help="Adds the Marketing Signage Use fee (from Lodge Settings) to the "
             "Event Costs.")
    x_exclusive_use = fields.Boolean(
        "Exclusive Use",
        help="Adds the Exclusive Use fee (from Lodge Settings) to the Event "
             "Costs.")
    x_bar_gratuity = fields.Monetary(
        "Bar Gratuity", currency_field='x_currency_id',
        help="Gratuity pool for the bar — split among bartender shifts.")
    x_kitchen_gratuity = fields.Monetary(
        "Kitchen Gratuity", currency_field='x_currency_id',
        help="Gratuity pool for the kitchen — split among kitchen shifts.")
    x_gratuity_amount = fields.Monetary(
        "Gratuity (total)", currency_field='x_currency_id',
        compute='_compute_gratuity_total', store=True,
        help="Bar + kitchen gratuity pools.")

    @api.depends('x_bar_gratuity', 'x_kitchen_gratuity')
    def _compute_gratuity_total(self):
        for rec in self:
            rec.x_gratuity_amount = (
                (rec.x_bar_gratuity or 0.0) + (rec.x_kitchen_gratuity or 0.0))

    # --- Gratuity distribution tracking (available vs. paid out) ------------
    x_bar_gratuity_distributed = fields.Monetary(
        "Bar Gratuity Distributed", currency_field='x_currency_id',
        compute='_compute_gratuity_distributed', store=True,
        help="Sum of gratuity shares already assigned to bartender shifts.")
    x_kitchen_gratuity_distributed = fields.Monetary(
        "Kitchen Gratuity Distributed", currency_field='x_currency_id',
        compute='_compute_gratuity_distributed', store=True,
        help="Sum of gratuity shares already assigned to kitchen shifts.")
    x_gratuity_distributed = fields.Monetary(
        "Gratuity Distributed", currency_field='x_currency_id',
        compute='_compute_gratuity_distributed', store=True,
        help="Total gratuity already paid out to shifts.")
    x_bar_gratuity_remaining = fields.Monetary(
        "Bar Gratuity Left", currency_field='x_currency_id',
        compute='_compute_gratuity_distributed', store=True,
        help="Bar pool minus what has been distributed to bartender shifts.")
    x_kitchen_gratuity_remaining = fields.Monetary(
        "Kitchen Gratuity Left", currency_field='x_currency_id',
        compute='_compute_gratuity_distributed', store=True,
        help="Kitchen pool minus what has been distributed to kitchen shifts.")
    x_gratuity_remaining = fields.Monetary(
        "Gratuity Left to Distribute", currency_field='x_currency_id',
        compute='_compute_gratuity_distributed', store=True,
        help="Total pool minus what has been distributed. Should be $0.00 "
             "after Distribute Gratuity if there are shifts in every role "
             "that has a pool.")

    @api.depends('x_bar_gratuity', 'x_kitchen_gratuity',
                 'x_attendance_ids.x_gratuity_share',
                 'x_attendance_ids.x_event_role')
    def _compute_gratuity_distributed(self):
        for rec in self:
            bar_paid = sum(rec.x_attendance_ids.filtered(
                lambda a: a.x_event_role == 'bartender').mapped(
                'x_gratuity_share'))
            kit_paid = sum(rec.x_attendance_ids.filtered(
                lambda a: a.x_event_role == 'kitchen').mapped(
                'x_gratuity_share'))
            rec.x_bar_gratuity_distributed = bar_paid
            rec.x_kitchen_gratuity_distributed = kit_paid
            rec.x_gratuity_distributed = bar_paid + kit_paid
            rec.x_bar_gratuity_remaining = (rec.x_bar_gratuity or 0.0) - bar_paid
            rec.x_kitchen_gratuity_remaining = (
                (rec.x_kitchen_gratuity or 0.0) - kit_paid)
            rec.x_gratuity_remaining = (
                rec.x_gratuity_amount - bar_paid - kit_paid)
    x_food_by_us = fields.Boolean("Food by Us")
    x_food_menu = fields.Text("Menu")
    x_food_request = fields.Boolean(
        "Food / Catering Request",
        help="The customer is requesting food or catering for the event.")
    x_catering_details = fields.Text(
        "Catering Details",
        help="What kind of catering the customer is requesting.")
    x_caterer_id = fields.Many2one('res.partner', string="Catered By")
    x_caterer_license_on_file = fields.Boolean("Caterer License on File")
    x_caterer_insurance_on_file = fields.Boolean("Caterer Insurance on File")
    x_extras = fields.Text("Extras Supplied")

    # Marker on a generated subtask so we never duplicate it
    x_checklist_code = fields.Char("Checklist Item Code", copy=False, index=True)

    # ------------------------------------------------------------------
    # Board approval — full audit trail
    # ------------------------------------------------------------------
    x_approval_state = fields.Selection(
        EVENT_APPROVAL_STATES, string="Approval Status",
        default='draft', tracking=True, copy=False, index=True,
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
    # Rooms already on a booking line — used to filter the room dropdown so
    # the same room can't be added twice on the Rooms tab.
    x_booked_room_ids = fields.Many2many(
        'maintenance.location', string="Booked Rooms",
        compute='_compute_booked_room_ids',
    )

    @api.depends('x_room_booking_ids.room_id')
    def _compute_booked_room_ids(self):
        for rec in self:
            rec.x_booked_room_ids = rec.x_room_booking_ids.mapped('room_id')
    # Multi-select of requested rooms — the website-form-builder-friendly way
    # to pick MANY rooms (renders as "Multiple Checkboxes"). Selecting rooms
    # here auto-creates the booking lines above with each room's default fees.
    x_requested_room_ids = fields.Many2many(
        'maintenance.location', 'elks_event_requested_room_rel',
        'task_id', 'room_id', string="Requested Rooms",
        domain="[('x_is_rentable', '=', True), ('active', '=', True)]",
        help="Pick one or more rooms; booking lines are created from these.",
    )
    x_room_income = fields.Monetary(
        "Room Income", currency_field='x_currency_id',
        compute='_compute_financials', store=True,
        help="Total of all room booking subtotals (rate + cleaning + service) "
             "as CHARGED (after any per-booking rate override).",
    )
    x_room_retail = fields.Monetary(
        "Room Rental (default/retail)", currency_field='x_currency_id',
        compute='_compute_financials', store=True,
        help="Total of the room's DEFAULT (retail) rates + fees, before any "
             "per-event override or member/COL discount. Drives the "
             "coordinator fee base and the member-discount amount.",
    )
    x_room_net = fields.Monetary(
        "Room Rental (billed)", currency_field='x_currency_id',
        compute='_compute_financials', store=True,
        help="What the customer is actually billed for the room after the "
             "member / Celebration-of-Life discount ($0 for a COL waiver).",
    )

    # ------------------------------------------------------------------
    # Cost lines (the 'PO section')
    # ------------------------------------------------------------------
    x_cost_line_ids = fields.One2many(
        'elks.event.cost.line', 'event_id', string="Event Costs",
    )
    x_cogs = fields.Monetary(
        "COGS (Cost of Goods)", currency_field='x_currency_id',
        compute='_compute_financials', store=True,
        help="Total cost to the lodge of the goods/services sold — the sum of "
             "the COGS column on the Event Costs lines. Feeds Total Costs "
             "(P&L).",
    )
    x_total_costs = fields.Monetary(
        "Total Costs", currency_field='x_currency_id',
        compute='_compute_financials', store=True,
    )
    x_net_income = fields.Monetary(
        "Net (P&L)", currency_field='x_currency_id',
        compute='_compute_financials', store=True,
        help="Revenue (pre-tax billed) minus Total Costs (COGS + POs + labor).",
    )
    x_discount_amount = fields.Monetary(
        "Discount Amount", currency_field='x_currency_id',
        compute='_compute_financials', store=True,
        help="Dollar value of the discount applied (list price minus billed).")
    # UBI (Unrelated Business Income): non-member room rentals are UBI. Reserve
    # a percentage of that room income for property tax (internal, not billed).
    x_ubi_room_income = fields.Monetary(
        "UBI Room Rental Income", currency_field='x_currency_id',
        compute='_compute_financials', store=True,
        help="Room rental income counted as UBI (non-member events only).")
    x_ubi_tax_reserve = fields.Monetary(
        "Property Tax Reserve (UBI)", currency_field='x_currency_id',
        compute='_compute_financials', store=True,
        help="Amount to set aside for property tax on the non-member room "
             "rental income, at the UBI / Property Tax % in Lodge Settings.")
    # P&L spending tallies by category — sum of the COGS on Event Costs lines
    # grouped by their type's category (the "budget for spending").
    x_cat_catering = fields.Monetary(
        "Catering (COGS)", currency_field='x_currency_id',
        compute='_compute_financials', store=True)
    x_cat_bar = fields.Monetary(
        "Bar (COGS)", currency_field='x_currency_id',
        compute='_compute_financials', store=True)
    x_cat_event = fields.Monetary(
        "Event Services (COGS)", currency_field='x_currency_id',
        compute='_compute_financials', store=True)
    x_cat_cleaning = fields.Monetary(
        "Cleaning (COGS)", currency_field='x_currency_id',
        compute='_compute_financials', store=True)
    x_cat_other = fields.Monetary(
        "Other / Gratuity (COGS)", currency_field='x_currency_id',
        compute='_compute_financials', store=True,
        help="COGS not in the four named categories — coordinator, gratuity "
             "pass-through, and any custom cost types. Included so the "
             "category breakdown reconciles to total COGS.")

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
    # Coordinator fee AUTO-TRACKS the suggested rate (room RETAIL x %, or the
    # fixed rate). It stays in sync as the rooms change UNLESS "Custom fee" is
    # ticked, which lets staff pin a manual amount (e.g. $0 for an unsponsored
    # event). Part of the customer's single Event Rental price.
    x_coordinator_fee = fields.Monetary(
        "Coordinator Fee", currency_field='x_currency_id', tracking=True,
        compute='_compute_coordinator_fee', store=True, readonly=False,
        help="Auto-fills from the room's retail rate x the coordinator %. Tick "
             "'Custom fee' to pin a manual amount. Included in the Event "
             "Rental price.",
    )
    x_coordinator_fee_manual = fields.Boolean(
        "Custom Coordinator Fee", default=False, tracking=True,
        help="When ticked, the coordinator fee is NOT auto-updated from the "
             "room rate — type any amount (e.g. $0 for an unsponsored event).")
    x_coordinator_employee_id = fields.Many2one(
        'hr.employee', string="Event Coordinator",
        help="The employee who coordinated this event. Use 'Pay Coordinator' "
             "to post the coordinator fee to their timecard as a flat payout.")
    x_coordinator_fee_paid = fields.Boolean(
        "Coordinator Fee Paid", copy=False,
        help="Set once the coordinator fee has been posted to the "
             "coordinator's timecard.")
    x_coordinator_fee_auto = fields.Monetary(
        "Suggested Coordinator Fee", currency_field='x_currency_id',
        compute='_compute_coordinator_auto',
        help="The fee the configured rate would produce (room income x %, or "
             "the fixed rate).",
    )
    x_coordinator_fee_variance = fields.Monetary(
        "Coordinator Fee Adj.", currency_field='x_currency_id',
        compute='_compute_coordinator_auto',
        help="Difference between the entered fee and the suggested amount.",
    )
    # Event-cost (customer charges) roll-up + tax/total mirrors of the quote.
    x_eventcosts_total = fields.Monetary(
        "Event Costs Total", currency_field='x_currency_id',
        compute='_compute_financials', store=True,
        help="Sum of the Event Costs lines (customer charges).",
    )
    x_taxable_subtotal = fields.Monetary(
        "Taxable Subtotal (goods)", currency_field='x_currency_id',
        compute='_compute_financials', store=True,
        help="Amount tax is charged on: rooms + Event Costs items flagged "
             "Taxable. Services (coordinator fee, non-taxable items) are "
             "excluded.",
    )
    x_nontaxable_subtotal = fields.Monetary(
        "Non-Taxable Subtotal (services)", currency_field='x_currency_id',
        compute='_compute_financials', store=True,
        help="Services and anything not flagged taxable — not taxed.",
    )
    x_event_tax_amount = fields.Monetary(
        "Tax", currency_field='x_currency_id',
        compute='_compute_financials', store=True,
    )
    x_event_grand_total = fields.Monetary(
        "Grand Total (incl. tax)", currency_field='x_currency_id',
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

    # Itemized staff-only quote (sale.order)
    x_sale_order_id = fields.Many2one(
        'sale.order', string="Event Quote", copy=False,
    )
    x_quote_total = fields.Monetary(
        "Quote Total", currency_field='x_currency_id',
        compute='_compute_quote_total',
        help="Untaxed total of the itemized quote — the customer-facing "
             "invoice total before member discount.",
    )
    x_calendar_event_id = fields.Many2one(
        'calendar.event', string="Calendar Booking", copy=False,
    )
    x_marketing_event_id = fields.Many2one(
        'event.event', string="Marketing Listing", copy=False,
        help="Public-facing listing in the Events app, auto-created when "
             "this is flagged as an Elks Event so it can be marketed.",
    )

    # ──────────────────────────────────────────────────────────────────
    # SECTION: After-action review (fields)
    # HUMAN: Once an event is over it moves to After Action. There the
    #        coordinator jots notes, reviews the P&L, and a feedback survey
    #        goes to the customer. These fields back that tab + the menu
    #        filter; the behaviour lives in the After-action methods section.
    # AI: x_is_after_action is STORED (filterable by the After-Action action);
    #     x_after_action_started guards the one-time survey/notes trigger.
    # ──────────────────────────────────────────────────────────────────
    x_is_after_action = fields.Boolean(
        "In After-Action", compute='_compute_is_after_action',
        store=True, index=True,
        help="True once the event reaches the After Action or Completed "
             "stage — drives the After-Action menu filter and P&L button.",
    )
    x_after_action_notes = fields.Text(
        "After-Action Notes", copy=False,
        help="Coordinator's notes/thoughts after the event — what went well, "
             "issues, follow-ups.",
    )
    x_after_action_started = fields.Boolean(
        "After-Action Triggered", copy=False, default=False,
        help="Guard: set once the survey + notes prompt have fired so they "
             "don't re-fire on later saves.",
    )
    x_customer_survey_input_id = fields.Many2one(
        'survey.user_input', string="Customer Survey Response", copy=False,
        help="The feedback survey sent to the customer for this event.",
    )
    x_customer_survey_state = fields.Selection(
        related='x_customer_survey_input_id.state', string="Survey Status",
        readonly=True,
    )

    @api.depends('stage_id')
    def _compute_is_after_action(self):
        aa = self.env.ref('elksevent.stage_after_action',
                          raise_if_not_found=False)
        done = self.env.ref('elksevent.stage_completed',
                            raise_if_not_found=False)
        stage_ids = {s.id for s in (aa, done) if s}
        for rec in self:
            rec.x_is_after_action = rec.stage_id.id in stage_ids

    # Labor PLANNING inputs — how long we expect each phase to take. These
    # drive the estimated hours (below), which drive the labor cost for the
    # P&L. Editable; the estimates auto-calc from them but can be overridden.
    x_plan_setup_hours = fields.Float(
        "Setup Hours (plan)", help="Planned event-staff setup hours.")
    x_plan_event_hours = fields.Float(
        "Event Hours (plan)", help="Planned event-staff hours during the event.")
    x_plan_teardown_hours = fields.Float(
        "Teardown / After Hours (plan)",
        help="Planned event-staff teardown / after hours.")
    x_plan_bar_hours = fields.Float(
        "Bartender Hours each (plan)",
        help="Planned hours per bartender (setup + service + teardown).")
    x_plan_cleaning_hours = fields.Float(
        "Cleaning Hours (plan)", help="Planned custodial/cleaning hours.")
    x_plan_kitchen_hours = fields.Float(
        "Kitchen Hours (plan)", help="Planned kitchen/catering service hours.")
    # Kitchen staffing broken out by role (like bartenders): a count and the
    # hours EACH, priced at the cook / kitchen-support rates in Lodge Settings.
    x_cook_count = fields.Integer(
        "Cooks", help="Number of cooks for this event.")
    x_cook_hours = fields.Float(
        "Hours per Cook", help="Planned hours each cook works.")
    x_kitchen_support_count = fields.Integer(
        "Kitchen Support", help="Number of kitchen-support staff (prep, "
        "serving, dish).")
    x_kitchen_support_hours = fields.Float(
        "Hours per Support", help="Planned hours each kitchen-support "
        "person works.")

    # Labor hours — estimated (from the plan) vs actual (from the timeclock).
    # Estimates are computed from the planning inputs but stay editable so
    # staff can override, and the True-up button sets them to the actuals.
    x_est_event_hours = fields.Float(
        "Est. Event-Staff Hours", compute='_compute_est_hours',
        store=True, readonly=False,
        help="Setup + Event + Teardown planned hours (editable).")
    x_est_cleaning_hours = fields.Float(
        "Est. Cleaning Hours", compute='_compute_est_hours',
        store=True, readonly=False,
        help="Planned custodial/cleaning hours (editable).")
    x_est_bartender_hours = fields.Float(
        "Est. Bartender Hours", compute='_compute_est_hours',
        store=True, readonly=False,
        help="Bartenders x planned hours each (editable).")
    x_est_cook_hours = fields.Float(
        "Est. Cook Hours", compute='_compute_est_hours',
        store=True, readonly=False,
        help="Cooks x planned hours each (editable).")
    x_est_kitchen_support_hours = fields.Float(
        "Est. Kitchen Support Hours", compute='_compute_est_hours',
        store=True, readonly=False,
        help="Kitchen support x planned hours each (editable).")
    x_actual_event_hours = fields.Float(
        "Actual Event-Staff Hours", compute='_compute_actual_hours',
        help="Event-staff hours actually worked (from the timeclock).")
    x_actual_cleaning_hours = fields.Float(
        "Actual Cleaning Hours", compute='_compute_actual_hours',
        help="Custodial hours actually worked (from the timeclock).")
    x_actual_bartender_hours = fields.Float(
        "Actual Bartender Hours", compute='_compute_actual_hours',
        help="Bartender hours actually worked (from the timeclock).")
    x_actual_kitchen_hours = fields.Float(
        "Actual Kitchen Hours", compute='_compute_actual_hours',
        help="Kitchen (cook + support) hours actually worked, from the "
             "timeclock 'kitchen' role.")
    x_actual_total_hours = fields.Float(
        "Total Event Hours", compute='_compute_actual_hours',
        help="All timeclock hours attached to this event (every role).")
    # The event's timeclock punches (assign a role per shift here).
    x_attendance_ids = fields.One2many(
        'hr.attendance', 'x_event_id', string="Event Attendance")
    # Planned staff roster (who is assigned to work, by role). Separate from the
    # actual timeclock punches above; an assigned person's punch auto-links to
    # this event on clock-in (see hr_attendance).
    x_staff_assignment_ids = fields.One2many(
        'elks.event.staff.assignment', 'event_id',
        string="Planned Staff Assignments")
    x_assigned_bartenders = fields.Integer(
        "Assigned Bartenders", compute='_compute_assigned_counts')
    x_assigned_cooks = fields.Integer(
        "Assigned Cooks", compute='_compute_assigned_counts')
    x_assigned_support = fields.Integer(
        "Assigned Kitchen Support", compute='_compute_assigned_counts')
    x_assigned_event_workers = fields.Integer(
        "Assigned Event Workers", compute='_compute_assigned_counts')
    x_assigned_cleaning = fields.Integer(
        "Assigned Cleaning", compute='_compute_assigned_counts')
    # How many of each requested position are still unfilled (requested minus
    # assigned, floored at zero).
    x_tofill_bartenders = fields.Integer(
        "Bartenders to Fill", compute='_compute_assigned_counts')
    x_tofill_cooks = fields.Integer(
        "Cooks to Fill", compute='_compute_assigned_counts')
    x_tofill_support = fields.Integer(
        "Kitchen Support to Fill", compute='_compute_assigned_counts')
    x_tofill_event_workers = fields.Integer(
        "Event Workers to Fill", compute='_compute_assigned_counts')
    x_tofill_cleaning = fields.Integer(
        "Cleaners to Fill", compute='_compute_assigned_counts')

    @api.depends('x_staff_assignment_ids.role',
                 'x_bartender_count', 'x_cook_count',
                 'x_kitchen_support_count', 'x_event_worker_count',
                 'x_cleaner_count')
    def _compute_assigned_counts(self):
        for rec in self:
            roles = rec.x_staff_assignment_ids.mapped('role')
            rec.x_assigned_bartenders = roles.count('bartender')
            rec.x_assigned_cooks = roles.count('cook')
            rec.x_assigned_support = roles.count('kitchen_support')
            rec.x_assigned_event_workers = roles.count('event_worker')
            rec.x_assigned_cleaning = roles.count('cleaning')
            rec.x_tofill_bartenders = max(
                (rec.x_bartender_count or 0) - rec.x_assigned_bartenders, 0)
            rec.x_tofill_cooks = max(
                (rec.x_cook_count or 0) - rec.x_assigned_cooks, 0)
            rec.x_tofill_support = max(
                (rec.x_kitchen_support_count or 0) - rec.x_assigned_support, 0)
            rec.x_tofill_event_workers = max(
                (rec.x_event_worker_count or 0)
                - rec.x_assigned_event_workers, 0)
            rec.x_tofill_cleaning = max(
                (rec.x_cleaner_count or 0) - rec.x_assigned_cleaning, 0)

    x_event_hours_variance = fields.Float(
        "Event Hours Variance", compute='_compute_actual_hours',
        help="Charged minus actual event-staff hours.")
    x_cleaning_hours_variance = fields.Float(
        "Cleaning Hours Variance", compute='_compute_actual_hours',
        help="Estimated minus actual cleaning hours.")
    x_est_labor_cost = fields.Monetary(
        "Estimated Labor Cost", currency_field='x_currency_id',
        compute='_compute_actual_hours',
        help="Estimated event-staff + cleaning hours x their rate — a P&L cost.")
    x_actual_labor_cost = fields.Monetary(
        "Actual Labor Cost", currency_field='x_currency_id',
        compute='_compute_actual_hours',
        help="Actual hours worked x their rate — a P&L cost.")
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

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Create override — file builder-form events into the project
    # HUMAN: A request submitted from the website Form builder doesn't carry
    #        a project, so it would land "loose". If a new task clearly looks
    #        like an event (has an event type/date/customer) and no project
    #        was set, drop it into the default Events project.
    # AI: @api.model_create_multi. Guard: not vals.get('project_id') AND any
    #     event-ish key present. Uses settings.x_event_project_id, else the
    #     base project_event_bookings. Normal tasks always carry project_id,
    #     so they're untouched.
    # ──────────────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        default_proj = -1  # sentinel: not looked up yet
        event_keys = ('x_event_type', 'x_event_date', 'x_customer_email',
                      'x_is_event', 'x_event_start_time_text')
        for vals in vals_list:
            if vals.get('project_id') or not any(k in vals for k in event_keys):
                continue
            if default_proj == -1:
                settings = self.env['elks.lodge.settings'].sudo().search(
                    [], limit=1)
                proj = settings.x_event_project_id if settings else False
                if not proj:
                    proj = self.env.ref(
                        'elksevent.project_event_bookings',
                        raise_if_not_found=False)
                default_proj = proj.id if proj else False
            if default_proj:
                vals['project_id'] = default_proj
        records = super().create(vals_list)
        records._sync_requested_rooms()
        records._reconcile_event_datetime()
        records.with_context(_labor_sync=True)._sync_labor_cost_lines()
        records.filtered('x_is_elks_event')._sync_marketing_event()
        return records

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get('_evt_dt_sync'):
            if 'date_deadline' in vals:
                # Header "Event Date" changed -> drive the tab date + start.
                self._push_deadline_to_event_time()
            elif vals.keys() & self._EVENT_TIME_KEYS:
                # Tab/web date or start changed -> rebuild the header.
                self._push_event_time_to_deadline()
        if (not self.env.context.get('_labor_sync')
                and (vals.keys() & self._LABOR_PLAN_KEYS)):
            self.with_context(_labor_sync=True)._sync_labor_cost_lines()
        if 'stage_id' in vals:
            self._close_children_on_fold()
            aa = self.env.ref('elksevent.stage_after_action',
                              raise_if_not_found=False)
            if aa:
                for rec in self:
                    if (rec.x_is_event and not rec.parent_id
                            and rec.stage_id.id == aa.id
                            and not rec.x_after_action_started):
                        rec._on_enter_after_action()
        # Resync the calendar.event window whenever any of the fields
        # that determine start/stop change. Without this the tentative
        # entry keeps whatever times it had at creation and drifts out
        # of sync when the coordinator later edits the schedule.
        if vals.keys() & self._CALENDAR_SYNC_KEYS:
            for rec in self:
                if rec.x_calendar_event_id:
                    try:
                        start, stop = rec._event_calendar_window()
                        if start and stop:
                            rec.x_calendar_event_id.sudo().write({
                                'start': start, 'stop': stop,
                            })
                    except Exception as e:  # noqa: BLE001
                        _logger.warning(
                            "Calendar resync failed for task %s: %s",
                            rec.id, e)
        # Keep the marketing (Events-app) listing in sync when the Elks
        # Event flag toggles or the schedule / name changes.
        if (not self.env.context.get('_marketing_sync')
                and (vals.keys() & self._MARKETING_SYNC_KEYS)):
            self._sync_marketing_event()
        return res

    # Fields whose change requires pushing new start/stop onto the
    # attached calendar.event entry.
    _CALENDAR_SYNC_KEYS = frozenset((
        'x_event_date', 'x_event_start_time', 'x_event_end_time',
        'x_event_start_sel', 'x_event_end_sel',
        'x_event_start_time_text', 'x_event_end_time_text',
        'date_deadline',
    ))

    def _close_children_on_fold(self):
        """When an event reaches a folded/completed stage, mark its still-open
        checklist sub-tasks as Done so nothing dangles."""
        Task = self.env['project.task']
        sf = Task._fields.get('state')
        done_key = False
        if sf and isinstance(sf.selection, (list, tuple)):
            done_key = next(
                (k for k, _ in sf.selection if 'done' in (k or '').lower()),
                False)
        if not done_key:
            return
        for rec in self:
            if not rec.x_is_event or rec.parent_id:
                continue
            if rec.stage_id and rec.stage_id.fold:
                kids = rec.child_ids.filtered(
                    lambda c: (c.state or '') != done_key
                    and 'cancel' not in (c.state or '').lower())
                if kids:
                    kids.write({'state': done_key})

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Event Date <-> Start Time <-> header link
    # HUMAN: The "Event Date" at the top of the form (the task deadline) and the
    #        Event Date + Start Time on the Event Details tab are ONE thing.
    #        Set either and the others follow — the top date+time fills the tab
    #        date and start time, and picking a start time on the tab updates
    #        the top field. End time is separate.
    # AI: date_deadline (datetime in v19) is the single display of event date +
    #     start. _push_deadline_to_event_time (header->tab) and
    #     _push_event_time_to_deadline (tab/web->header) keep them equal; both
    #     write with _evt_dt_sync in context so the paired write never loops.
    #     _reconcile_event_datetime seeds whichever side is empty (create +
    #     migration). x_event_end_* is independent.
    # ──────────────────────────────────────────────────────────────────
    _EVENT_TIME_KEYS = frozenset((
        'x_event_date', 'x_event_start_time',
        'x_event_start_sel', 'x_event_start_time_text'))

    def _push_deadline_to_event_time(self):
        for rec in self:
            dl = rec.date_deadline
            if not rec.x_is_event or not dl:
                continue
            upd = {}
            if hasattr(dl, 'hour'):
                # UTC datetime -> user-local date + start time.
                dl_local = fields.Datetime.context_timestamp(rec, dl)
                d = dl_local.date()
                upd['x_event_start_time'] = dl_local.hour + dl_local.minute / 60.0
            else:
                d = dl
            if rec.x_event_date != d:
                upd['x_event_date'] = d
            if upd:
                rec.with_context(_evt_dt_sync=True).write(upd)

    def _push_event_time_to_deadline(self):
        from datetime import datetime, time as _dtime
        dl_is_dt = (self.env['project.task']._fields['date_deadline'].type
                    == 'datetime')
        for rec in self:
            if not rec.x_is_event or not rec.x_event_date:
                continue
            if dl_is_dt:
                st = rec.x_event_start_time or 0.0
                h = min(int(st), 23)
                mnt = min(int(round((st - int(st)) * 60)), 59)
                # The date + start time are the user's LOCAL wall-clock; store
                # date_deadline in UTC so it round-trips back to the same time.
                naive_local = datetime.combine(rec.x_event_date, _dtime(h, mnt))
                tz = pytz.timezone(rec.env.user.tz or 'UTC')
                new_dl = tz.localize(naive_local).astimezone(
                    pytz.utc).replace(tzinfo=None)
            else:
                new_dl = rec.x_event_date
            if rec.date_deadline != new_dl:
                rec.with_context(_evt_dt_sync=True).write(
                    {'date_deadline': new_dl})

    def _reconcile_event_datetime(self):
        """Seed whichever side is set. Used on create + in migration."""
        for rec in self:
            if not rec.x_is_event:
                continue
            if rec.date_deadline:
                rec._push_deadline_to_event_time()
            elif rec.x_event_date:
                rec._push_event_time_to_deadline()

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Requested rooms <-> booking lines (full reconcile)
    # HUMAN: The "Requested Rooms" checkboxes are the room selector. Ticking a
    #        room adds a booking line (with its default fees); UN-ticking it
    #        removes the line. Use the booking list below to adjust prices.
    # AI: Bidirectional + idempotent: removes bookings whose room is no longer
    #     requested and any duplicates, then adds requested rooms that aren't
    #     booked. Runs from create() and write() (when x_requested_room_ids is
    #     in vals) on PERSISTED records, so unlink/create are safe.
    # ──────────────────────────────────────────────────────────────────
    def _sync_requested_rooms(self):
        Booking = self.env['elks.event.room.booking']
        for rec in self:
            # Only acts when rooms were picked via the m2m (the website builder
            # form). Backend staff manage rooms directly on the booking list,
            # so an empty selection must never remove their lines.
            if not rec.x_requested_room_ids:
                continue
            requested_ids = set(rec.x_requested_room_ids.ids)
            # Drop bookings for un-requested rooms + collapse duplicates.
            seen = set()
            to_remove = Booking
            for b in rec.x_room_booking_ids:
                rid = b.room_id.id
                if rid not in requested_ids or rid in seen:
                    to_remove |= b
                else:
                    seen.add(rid)
            if to_remove:
                to_remove.unlink()
            # Add requested rooms that aren't booked yet.
            for room in rec.x_requested_room_ids:
                if room.id not in seen:
                    Booking.create({
                        'event_id': rec.id,
                        'room_id': room.id,
                        'rate': room.x_room_rate,
                        'cleaning_fee': room.x_cleaning_fee,
                        'service_fee': room.x_service_fee,
                    })
                    seen.add(room.id)

    # ------------------------------------------------------------------
    # Computed: is_event
    # ------------------------------------------------------------------
    @api.depends('project_id', 'project_id.x_is_event_project')
    def _compute_is_event(self):
        """True if the task's project is an events project.

        Robust signal first: any project flagged `x_is_event_project`. We also
        keep the legacy fallbacks (the base project + the 'Events YYYY-YYYY'
        name pattern) so existing year folders created before the flag still
        classify correctly.
        """
        base_project = self.env.ref(
            'elksevent.project_event_bookings', raise_if_not_found=False,
        )
        event_project_ids = set()
        if base_project:
            event_project_ids.add(base_project.id)
        # Flagged event projects (the robust, name-independent signal)
        flagged = self.env['project.project'].sudo().search([
            ('x_is_event_project', '=', True),
        ])
        event_project_ids.update(flagged.ids)
        # Legacy: Elk Year projects detected by naming convention
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
                # Event runs past midnight (e.g. 22:00 → 01:00): add 24h
                if dur < 0:
                    dur += 24.0
                rec.x_event_duration = dur
            else:
                rec.x_event_duration = 0.0

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Human-friendly time text (website + display)
    # HUMAN: Lets people type "10:30 am" / "2 pm" for the start/end times.
    #        The real value still lives in the Float fields; these just
    #        translate to/from a readable string. Point any website form-
    #        builder time field at *_text (a Float renders as a number box
    #        and rejects "10:30 am").
    # AI: store=True compute (Float -> text) + inverse (text -> Float).
    #     Parser accepts h[:mm][am|pm], 12h or 24h; unparseable text is
    #     ignored (leaves the Float untouched) so a bad entry never crashes.
    # ──────────────────────────────────────────────────────────────────
    @staticmethod
    def _parse_time_text(txt):
        """Parse '10:30 am' / '2 pm' / '14:30' -> decimal hours, or None."""
        if not txt:
            return None
        m = re.match(r'^\s*(\d{1,2})(?::(\d{2}))?\s*([ap]m)?\s*$',
                     txt.strip().lower())
        if not m:
            return None
        h = int(m.group(1))
        mn = int(m.group(2) or 0)
        ap = m.group(3)
        if ap == 'pm' and h != 12:
            h += 12
        elif ap == 'am' and h == 12:
            h = 0
        if h > 23 or mn > 59:
            return None
        return h + mn / 60.0

    @staticmethod
    def _format_time_text(val):
        """Format decimal hours -> '10:30 AM' (blank for 0/empty)."""
        if not val:
            return ''
        h = int(val)
        mn = int(round((val - h) * 60))
        if mn == 60:
            h += 1
            mn = 0
        ap = 'AM'
        h12 = h
        if h == 0:
            h12 = 12
        elif h == 12:
            ap = 'PM'
        elif h > 12:
            h12 = h - 12
            ap = 'PM'
        return f"{h12}:{mn:02d} {ap}"

    @api.depends('x_event_start_time', 'x_event_end_time')
    def _compute_event_time_text(self):
        for rec in self:
            rec.x_event_start_time_text = rec._format_time_text(
                rec.x_event_start_time)
            rec.x_event_end_time_text = rec._format_time_text(
                rec.x_event_end_time)

    def _inverse_event_time_text(self):
        for rec in self:
            start = rec._parse_time_text(rec.x_event_start_time_text)
            if start is not None:
                rec.x_event_start_time = start
            end = rec._parse_time_text(rec.x_event_end_time_text)
            if end is not None:
                rec.x_event_end_time = end

    # ── Selectable 15-minute time dropdowns (backend form) ──────────────
    @api.model
    def _event_time_options(self):
        """Return [(minutes_str, '10:30 AM'), ...] in 15-minute steps."""
        opts = []
        for slot in range(96):  # 24h * 4 quarter-hours
            mins = slot * 15
            h, m = divmod(mins, 60)
            ap = 'AM' if h < 12 else 'PM'
            h12 = h % 12 or 12
            opts.append((str(mins), f"{h12}:{m:02d} {ap}"))
        return opts

    @staticmethod
    def _float_to_minkey(val):
        """Decimal hours -> nearest-15-min key ('630' for 10:30), or False."""
        if not val:
            return False
        mins = int(round((val * 60) / 15.0)) * 15
        if mins >= 1440:
            mins = 1425
        return str(mins)

    @api.depends('x_event_start_time', 'x_event_end_time')
    def _compute_event_time_sel(self):
        for rec in self:
            rec.x_event_start_sel = rec._float_to_minkey(rec.x_event_start_time)
            rec.x_event_end_sel = rec._float_to_minkey(rec.x_event_end_time)

    def _inverse_event_time_sel(self):
        for rec in self:
            if rec.x_event_start_sel:
                rec.x_event_start_time = int(rec.x_event_start_sel) / 60.0
            if rec.x_event_end_sel:
                rec.x_event_end_time = int(rec.x_event_end_sel) / 60.0

    @api.onchange('partner_id')
    def _onchange_partner_fill_host(self):
        """Auto-fill the Host name/phone/email from the chosen Contact.

        Only fills blanks so a manually typed host detail is never clobbered.
        The Contact may be a person or a company (any res.partner).
        """
        p = self.partner_id
        if not p:
            return
        if not self.x_customer_name:
            self.x_customer_name = p.name
        if not self.x_customer_phone:
            self.x_customer_phone = p.phone or p.mobile
        if not self.x_customer_email:
            self.x_customer_email = p.email

    @api.onchange('date_deadline')
    def _onchange_deadline_sets_event_date(self):
        """The header "Event Date" (date_deadline) drives the workflow.

        The header field IS the task's date_deadline (relabeled "Event Date").
        The rest of the module keys off x_event_date (+ start time), so mirror
        the header into those here: setting the header fills the event date and
        seeds the start time from the header's time. This is why submitting no
        longer complains that the event date is unset when it's clearly in the
        header. Only seeds a BLANK start time, so a start picked on the tab is
        never overwritten.
        """
        dl = self.date_deadline
        if not dl:
            return
        # date_deadline is a UTC datetime; convert to the user's timezone so the
        # tab date + start time show the LOCAL wall-clock they entered.
        if hasattr(dl, 'hour'):
            dl_local = fields.Datetime.context_timestamp(self, dl)
            self.x_event_date = dl_local.date()
            self.x_event_start_time = dl_local.hour + dl_local.minute / 60.0
        else:
            self.x_event_date = dl

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

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Computed financials (room income, coordinator fee, totals)
    # HUMAN: Works out the headline numbers — what the customer is charged,
    #        the deposit/balance, costs, and budget left. When a quote exists
    #        it uses the quote total (minus member discount); otherwise it
    #        falls back to the older room+coordinator math.
    # AI: Stored compute. Cannot @api.depends on settings, so member-discount %
    #     / coordinator config changes don't retro-recompute existing events.
    #     x_total_billed feeds the PO budget guard and the assessor net income.
    # ──────────────────────────────────────────────────────────────────
    @api.depends('x_plan_setup_hours', 'x_plan_event_hours',
                 'x_plan_teardown_hours', 'x_plan_bar_hours',
                 'x_plan_cleaning_hours', 'x_bartender_count',
                 'x_event_workers_use', 'x_event_worker_count',
                 'x_event_worker_hours',
                 'x_cleaning_use', 'x_cleaner_count', 'x_cleaner_hours',
                 'x_cook_count', 'x_cook_hours',
                 'x_kitchen_support_count', 'x_kitchen_support_hours')
    def _compute_est_hours(self):
        """Estimated labor hours from the planning inputs (by role)."""
        for rec in self:
            # Event workers (count x hours each) drive event-staff hours when
            # entered; otherwise fall back to the setup/event/teardown plan.
            if rec.x_event_workers_use and rec.x_event_worker_count:
                rec.x_est_event_hours = (
                    (rec.x_event_worker_count or 0)
                    * (rec.x_event_worker_hours or 0.0))
            else:
                rec.x_est_event_hours = ((rec.x_plan_setup_hours or 0.0)
                                         + (rec.x_plan_event_hours or 0.0)
                                         + (rec.x_plan_teardown_hours or 0.0))
            rec.x_est_bartender_hours = (
                (rec.x_bartender_count or 0) * (rec.x_plan_bar_hours or 0.0))
            # Cleaners (count x hours each) drive cleaning hours when entered;
            # otherwise fall back to the planned cleaning hours.
            if rec.x_cleaning_use and rec.x_cleaner_count:
                rec.x_est_cleaning_hours = (
                    (rec.x_cleaner_count or 0) * (rec.x_cleaner_hours or 0.0))
            else:
                rec.x_est_cleaning_hours = rec.x_plan_cleaning_hours or 0.0
            rec.x_est_cook_hours = (
                (rec.x_cook_count or 0) * (rec.x_cook_hours or 0.0))
            rec.x_est_kitchen_support_hours = (
                (rec.x_kitchen_support_count or 0)
                * (rec.x_kitchen_support_hours or 0.0))

    @api.depends('x_guest_count')
    def _compute_bartenders(self):
        """One bartender per N guests (N from Settings, default 75)."""
        settings = self._event_settings()
        per = (settings.x_guests_per_bartender
               if settings and settings.x_guests_per_bartender else 75) or 75
        for rec in self:
            g = rec.x_guest_count or 0
            rec.x_bartender_count = math.ceil(g / per) if g else 0

    def _coordinator_auto_value(self):
        """The suggested coordinator fee: the fixed rate, or the coordinator %
        of the room's DEFAULT (retail) rate + fees — never the charged/billed
        (discounted) amount, so a member or COL discount can't shrink the pay.
        Computed from the bookings directly (not x_room_retail) to avoid a
        dependency cycle with the financials compute."""
        self.ensure_one()
        settings = self._event_settings()
        if settings and settings.x_coordinator_fee_type == 'fixed':
            return settings.x_coordinator_fixed_rate or 0.0
        pct = self.x_coordinator_fee_pct or 0.0
        if settings and settings.x_coordinator_percentage:
            pct = settings.x_coordinator_percentage
        retail = sum(
            (b.room_id.x_room_rate or 0.0)
            + (b.room_id.x_cleaning_fee or 0.0)
            + (b.room_id.x_service_fee or 0.0)
            for b in self.x_room_booking_ids)
        if not retail:
            retail = self.x_room_rental_rate or 0.0
        return retail * (pct / 100.0)

    @api.depends('x_room_booking_ids.room_id', 'x_room_booking_ids.subtotal',
                 'x_room_rental_rate', 'x_coordinator_fee_pct',
                 'x_coordinator_fee_manual')
    def _compute_coordinator_fee(self):
        """Auto-track the suggested fee unless 'Custom fee' is ticked."""
        for rec in self:
            if rec.x_coordinator_fee_manual:
                # Preserve the manually pinned amount.
                rec.x_coordinator_fee = rec.x_coordinator_fee
            else:
                rec.x_coordinator_fee = rec._coordinator_auto_value()

    @api.depends('x_room_retail', 'x_coordinator_fee_pct', 'x_coordinator_fee')
    def _compute_coordinator_auto(self):
        """Suggested coordinator fee (retail-based) + the variance vs the fee
        actually entered (non-zero only when 'Custom fee' pins a different
        amount)."""
        for rec in self:
            auto = rec._coordinator_auto_value()
            rec.x_coordinator_fee_auto = auto
            rec.x_coordinator_fee_variance = (rec.x_coordinator_fee or 0.0) - auto

    @api.onchange('x_discount_reason')
    def _onchange_discount_reason(self):
        """Member and Non-Profit default to 50% off (editable). Clearing the
        reason clears the percent + amount."""
        if self.x_discount_reason in ('member', 'nonprofit'):
            if self.x_discount_type == 'percent' and not self.x_discount_pct:
                self.x_discount_pct = 50.0
        elif not self.x_discount_reason:
            self.x_discount_pct = 0.0
            self.x_discount_value = 0.0

    def _event_room_discount(self):
        """The room discount, which applies to the ROOM ONLY — never event
        costs or the coordinator fee.

        Returns (rooms_net_billed, discount_from_retail):
        - The room BILLS at the price set on the Rooms tab (the booking
          subtotals). Set it to $0 there to give the space away (e.g. a
          Celebration of Life); set a reduced rate for a member courtesy.
        - An optional manual reason/percent/amount reduces the set price
          further.
        - The reported discount is the room's RETAIL default minus what is
          actually billed, i.e. exactly what was taken off the room rental.
        """
        self.ensure_one()
        bookings = self.x_room_booking_ids
        room_income = (sum(bookings.mapped('subtotal'))
                       or self.x_room_rental_rate or 0.0)
        room_retail = sum(
            (b.room_id.x_room_rate or 0.0)
            + (b.room_id.x_cleaning_fee or 0.0)
            + (b.room_id.x_service_fee or 0.0)
            for b in bookings) or room_income
        # The room bills at the PRICE SET ON THE ROOMS TAB — that set price IS
        # the room discount decision (set it to $0 to give the space away for
        # a Celebration of Life, or to a reduced rate for a member courtesy).
        # An optional manual reason/percent/amount reduces the set price on top.
        # Member Rental or Non-Profit gives an automatic 50% off the room rate.
        # A manual discount reason (if set) overrides the automatic one.
        auto_half = self.x_is_member or self.x_is_nonprofit
        if self.x_discount_reason and self.x_discount_type == 'amount':
            rooms_net = max(room_income - (self.x_discount_value or 0.0), 0.0)
        elif self.x_discount_reason:
            rooms_net = room_income * (
                1.0 - (self.x_discount_pct or 0.0) / 100.0)
        elif auto_half:
            rooms_net = room_income * 0.5
        else:
            rooms_net = room_income
        # Discount = the room's retail default minus what we actually charge
        # (i.e. exactly the amount taken off the room rental).
        discount = max(room_retail - rooms_net, 0.0)
        return rooms_net, discount

    def _event_discount_label(self):
        """Human label for the room-rental discount (invoice / Financials)."""
        self.ensure_one()
        labels = {
            'member': _("Member Discount"),
            'nonprofit': _("Non-Profit Discount"),
            'officer_board': _("Officer / Board Approved Discount"),
            'other': _("Discount"),
        }
        if self.x_discount_reason:
            return labels[self.x_discount_reason]
        # Automatic 50% for member / non-profit (no manual reason set).
        if self.x_is_member:
            return _("Member Discount (50%)")
        if self.x_is_nonprofit:
            return _("Non-Profit Discount (50%)")
        is_col = (self.x_event_type == 'memorial' and self.x_member_number
                  and self.x_member_number != '000000000')
        if is_col:
            # $0 room = a full waiver; a reduced (non-zero) room is a courtesy.
            _rooms_net, _disc = self._event_room_discount()
            if _rooms_net <= 0.005:
                return _("Celebration of Life (member) - facility waived")
            return _("Celebration of Life (member) - room courtesy")
        return _("Room Rental Discount")

    @api.constrains('x_discount_reason', 'x_discount_note',
                    'x_discount_pct', 'x_discount_value', 'x_discount_type')
    def _check_discount_note(self):
        """A discount always needs a written reason."""
        for rec in self:
            applied = rec.x_discount_reason and (
                (rec.x_discount_type == 'amount' and rec.x_discount_value)
                or (rec.x_discount_type == 'percent' and rec.x_discount_pct))
            if applied and not (rec.x_discount_note or '').strip():
                raise ValidationError(_(
                    "Please add a Discount Note explaining why this event is "
                    "getting a discount."))

    # ── Paid labor helpers (volunteers cost nothing) ───────────────────
    @staticmethod
    def _elks_is_volunteer_att(att):
        """Volunteer = employee in the 'Volunteers' department (the lodge-wide
        payroll exclusion). Their event hours are not a labor cost."""
        emp = att.employee_id
        return bool(emp and emp.department_id
                    and emp.department_id.name == 'Volunteers')

    def _event_role_rates(self, settings):
        """Per-hour cost rate by attendance role, from Lodge Settings."""
        if not settings:
            return {'event': 0.0, 'bartender': 0.0,
                    'kitchen': 0.0, 'custodial': 0.0}
        return {
            'event': settings.x_event_staff_rate or 0.0,
            'bartender': settings.x_bartender_rate or 0.0,
            'kitchen': (settings.x_cook_rate or settings.x_kitchen_rate or 0.0),
            'custodial': settings.x_custodial_rate or 0.0,
        }

    def _att_labor_rate(self, att, rates):
        """Per-hour cost for one attendance shift.

        Uses the employee's own payroll rate (hr.employee.hourly_cost — the
        "Wage" on their profile) so the P&L reflects real labor cost. Falls
        back to the role rate from Lodge Settings when the employee has no
        rate set. Volunteers are handled by the caller (cost nothing).
        """
        emp = att.employee_id
        rate = getattr(emp, 'hourly_cost', 0.0) or 0.0
        if not rate:
            rate = rates.get(att.x_event_role or 'event', 0.0)
        return rate

    def _paid_labor_by_category(self):
        """Actual PAID labor cost (non-volunteer timeclock hours x the
        employee's payroll rate) grouped by P&L category. Volunteer hours are
        excluded (they cost nothing)."""
        self.ensure_one()
        rates = self._event_role_rates(self._event_settings())
        role_cat = {'event': 'event', 'bartender': 'bar',
                    'kitchen': 'catering', 'custodial': 'cleaning'}
        out = {'event': 0.0, 'bar': 0.0, 'catering': 0.0, 'cleaning': 0.0}
        for att in self.x_attendance_ids:
            if self._elks_is_volunteer_att(att):
                continue
            cat = role_cat.get(att.x_event_role or 'event')
            if cat:
                out[cat] += (att.worked_hours or 0.0) * self._att_labor_rate(
                    att, rates)
        return out

    @api.depends(
        'x_room_booking_ids.subtotal',
        'x_cost_line_ids.total',
        'x_cost_line_ids.x_taxable',
        'x_coordinator_fee',
        'x_room_rental_rate',
        'x_cost_line_ids.x_line_cogs',
        'x_cost_line_ids.cost_type_id',
        'x_cost_line_ids.x_auto_source',
        'x_deposit_amount', 'x_deposit_received',
        'x_purchase_order_ids.amount_total',
        'x_purchase_order_ids.state',
        'x_attendance_ids.worked_hours', 'x_attendance_ids.x_event_role',
        'x_attendance_ids.employee_id',
        'x_is_member', 'x_is_nonprofit', 'x_event_type', 'x_member_number',
        'x_discount_reason', 'x_discount_pct',
        'x_discount_type', 'x_discount_value',
    )
    def _compute_financials(self):
        settings = self._event_settings()
        # Event tax rate (simple percentage sale tax) from settings.
        rate = 0.0
        tax = settings.x_event_tax_id if settings else False
        if tax and getattr(tax, 'amount_type', 'percent') == 'percent':
            rate = (tax.amount or 0.0) / 100.0
        disc_pct = (settings.x_member_discount_pct
                    if settings and settings.x_member_discount_pct else 0.0)
        for rec in self:
            # Room income from room booking lines (as CHARGED)
            room_income = sum(rec.x_room_booking_ids.mapped('subtotal'))
            if not room_income and rec.x_room_rental_rate:
                room_income = rec.x_room_rental_rate
            rec.x_room_income = room_income
            # Room RETAIL = the room master's default rate + fees, ignoring any
            # per-booking override. Used for the coordinator fee base and the
            # member/COL discount (retail vs actual).
            room_retail = sum(
                (b.room_id.x_room_rate or 0.0)
                + (b.room_id.x_cleaning_fee or 0.0)
                + (b.room_id.x_service_fee or 0.0)
                for b in rec.x_room_booking_ids)
            if not room_retail:
                room_retail = room_income or rec.x_room_rental_rate or 0.0
            rec.x_room_retail = room_retail

            # Event Costs are now customer CHARGES (roll into the price).
            rec.x_eventcosts_total = sum(rec.x_cost_line_ids.mapped('total'))
            ec_taxable = sum(
                c.total for c in rec.x_cost_line_ids if c.x_taxable)
            ec_nontax = rec.x_eventcosts_total - ec_taxable

            # The member / Celebration-of-Life discount applies to the ROOM
            # ONLY — never to event costs or the coordinator fee. rooms_net is
            # the billed room after that discount (0 for a COL waiver).
            rooms_net, room_disc = rec._event_room_discount()
            rec.x_room_net = rooms_net

            # Taxable base = GOODS (rooms + items flagged Taxable). Event costs
            # are billed at FULL (no discount); services (coordinator + items
            # marked non-taxable) are exempt. Computed LIVE so tax updates the
            # instant a Taxable box changes.
            rec.x_taxable_subtotal = rooms_net + ec_taxable
            rec.x_nontaxable_subtotal = (
                ec_nontax + (rec.x_coordinator_fee or 0.0))
            rec.x_event_tax_amount = rec.x_taxable_subtotal * rate

            customer_total = rec.x_taxable_subtotal + rec.x_nontaxable_subtotal
            rec.x_event_grand_total = customer_total + rec.x_event_tax_amount
            rec.x_total_billed = customer_total
            rec.x_subtotal = customer_total
            rec.x_event_total = customer_total

            # Real costs to the lodge = vendor POs + goods COGS + labor.
            # Labor is PROJECTED then ACTUAL: before anyone works a category we
            # use the estimated labor COGS on the cost lines; once someone
            # clocks in for that category we switch to the actual PAID labor
            # (which is $0 if only volunteers worked it — volunteers cost
            # nothing).
            po_total = sum(
                rec.x_purchase_order_ids.filtered(
                    lambda p: p.state != 'cancel'
                ).mapped('amount_total')
            )
            cats = {'catering': 0.0, 'bar': 0.0, 'event': 0.0, 'cleaning': 0.0}
            goods_cogs = 0.0
            est_labor = {'catering': 0.0, 'bar': 0.0, 'event': 0.0,
                         'cleaning': 0.0, 'other': 0.0}
            for c in rec.x_cost_line_ids:
                cat = c.cost_type_id.category
                cogs = c.x_line_cogs or 0.0
                if (c.x_auto_source or '').startswith('labor'):
                    # Estimated labor cost, held by category as the projection.
                    est_labor[cat if cat in est_labor else 'other'] += cogs
                    continue
                goods_cogs += cogs
                if cat in cats:
                    cats[cat] += cogs
            # Which categories have anyone clocked in (paid OR volunteer)?
            role_cat = {'event': 'event', 'bartender': 'bar',
                        'kitchen': 'catering', 'custodial': 'cleaning'}
            worked = set()
            for att in rec.x_attendance_ids:
                wc = role_cat.get(att.x_event_role or 'event')
                if wc:
                    worked.add(wc)
            # Actual PAID labor (non-volunteer attendance) by category.
            paid = rec._paid_labor_by_category()
            labor_total = est_labor['other']
            for cat in ('catering', 'bar', 'event', 'cleaning'):
                lab = (paid.get(cat, 0.0) if cat in worked
                       else est_labor.get(cat, 0.0))
                cats[cat] += lab
                labor_total += lab

            rec.x_cogs = goods_cogs + labor_total
            rec.x_cat_catering = cats['catering']
            rec.x_cat_bar = cats['bar']
            rec.x_cat_event = cats['event']
            rec.x_cat_cleaning = cats['cleaning']
            # Everything else (coordinator, gratuity pass-through, custom types)
            # so the category breakdown always reconciles to total COGS.
            rec.x_cat_other = (rec.x_cogs or 0.0) - (
                cats['catering'] + cats['bar']
                + cats['event'] + cats['cleaning'])
            rec.x_total_costs = (rec.x_cogs or 0.0) + po_total
            rec.x_net_income = rec.x_total_billed - rec.x_total_costs

            # Discount dollar value = the ROOM's retail default minus the
            # billed room (event costs are never discounted).
            rec.x_discount_amount = room_disc

            # UBI: non-member room rentals are Unrelated Business Income.
            # Reserve the property-tax percentage on that room income.
            ubi_pct = ((settings.x_ubi_tax_pct or 0.0) / 100.0) if settings else 0.0
            rec.x_ubi_room_income = 0.0 if rec.x_is_member else (room_income or 0.0)
            rec.x_ubi_tax_reserve = rec.x_ubi_room_income * ubi_pct

            # Balance due
            deposit = rec.x_deposit_amount if rec.x_deposit_received else 0.0
            rec.x_balance_due = rec.x_total_billed - deposit

            # Budget remaining (billed minus real costs)
            rec.x_budget_remaining = rec.x_total_billed - rec.x_total_costs

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
    # Computed: net income + UBI flag (for the Assessor report)
    # ------------------------------------------------------------------
    @api.depends(
        'x_total_billed', 'x_total_costs',
        'x_is_event', 'x_is_elks_event', 'x_is_member',
    )
    def _compute_is_ubi(self):
        for rec in self:
            rec.x_event_net_income = (rec.x_total_billed or 0.0) - (
                rec.x_total_costs or 0.0)
            rec.x_is_ubi = (
                rec.x_is_event
                and not rec.x_is_elks_event
                and not rec.x_is_member
                and rec.x_event_net_income != 0.0
            )

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Double-booking guard
    # HUMAN: Stops two events from claiming the same room at overlapping
    #        times on the same day — unless a manager ticks the override.
    # AI: @api.constrains scans elks.event.room.booking for same room+date,
    #     skips rejected events and x_double_booking_override. Blank end time
    #     is treated as an open/all-day window by _event_times_overlap.
    # ──────────────────────────────────────────────────────────────────
    @staticmethod
    def _event_times_overlap(s1, e1, s2, e2):
        """True if two [start, end) windows overlap. Blank end = open day."""
        s1 = s1 or 0.0
        s2 = s2 or 0.0
        e1 = e1 or 24.0
        e2 = e2 or 24.0
        return s1 < e2 and s2 < e1

    @api.constrains(
        'x_event_date', 'x_event_start_time', 'x_event_end_time',
        'x_room_booking_ids', 'x_double_booking_override',
    )
    def _check_double_booking(self):
        for rec in self:
            if (not rec.x_is_event or rec.x_double_booking_override
                    or not rec.x_event_date or not rec.x_room_booking_ids):
                continue
            room_ids = rec.x_room_booking_ids.mapped('room_id').ids
            if not room_ids:
                continue
            others = self.env['elks.event.room.booking'].search([
                ('event_id', '!=', rec.id),
                ('room_id', 'in', room_ids),
                ('event_id.x_event_date', '=', rec.x_event_date),
                ('event_id.x_approval_state', '!=', 'rejected'),
            ])
            for ob in others:
                oe = ob.event_id
                if rec._event_times_overlap(
                    rec.x_event_start_time, rec.x_event_end_time,
                    oe.x_event_start_time, oe.x_event_end_time,
                ):
                    raise ValidationError(_(
                        "Double booking: room '%(room)s' on %(date)s overlaps "
                        "event '%(other)s'.\n\nTick 'Double-Booking Override' "
                        "(managers) if this is intentional.",
                        room=ob.room_id.name,
                        date=rec.x_event_date,
                        other=oe.name,
                    ))

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Board approval workflow
    # HUMAN: The path every event walks: Draft -> Board -> Approved (or
    #        Rejected). A Coordinator can submit but can't approve; an Event
    #        Officer does the Board approval (which is FINAL) and holds the
    #        budget / double-booking overrides. (There is no Floor vote step.)
    # AI: state = x_approval_state ('floor' retained in the enum only for
    #     legacy data). action_board_approve -> _finalize_event_approval, which
    #     fans out: booked stage, checklist subtasks, calendar promote,
    #     approval email, insurance to-do.
    # ──────────────────────────────────────────────────────────────────
    def _ensure_approver(self, group_xmlid, action_label):
        """Raise unless the current user belongs to the approver group."""
        if not self.env.user.has_group(group_xmlid):
            raise UserError(_(
                "You do not have permission to %s. This is restricted to the "
                "designated approvers (assigned under Settings → Users).",
                action_label,
            ))

    def action_submit(self):
        """Submit the event for Board review (draft -> board)."""
        for rec in self:
            if rec.x_approval_state != 'draft':
                raise UserError(_("Only draft events can be submitted."))
            # The header "Event Date" is date_deadline; mirror it into
            # x_event_date if the onchange didn't run (programmatic path, stale
            # form) so a booking with a header date submits cleanly.
            if not rec.x_event_date and rec.date_deadline:
                dl = rec.date_deadline
                if hasattr(dl, 'hour'):
                    dl_local = fields.Datetime.context_timestamp(rec, dl)
                    rec.x_event_date = dl_local.date()
                    if not rec.x_event_start_time:
                        rec.x_event_start_time = (
                            dl_local.hour + dl_local.minute / 60.0)
                else:
                    rec.x_event_date = dl
            if not rec.x_event_date:
                raise UserError(_(
                    "Please set the event date (top-right) before submitting "
                    "for approval."
                ))
            vals = {
                'x_approval_state': 'board',
                'x_board_submitted_date': fields.Date.context_today(rec),
            }
            submitted_stage = self.env.ref(
                'elksevent.stage_submitted_to_board', raise_if_not_found=False,
            )
            if submitted_stage:
                vals['stage_id'] = submitted_stage.id
            rec.write(vals)
            rec.message_post(
                body=_("Submitted to <b>Board</b> for review by %s.",
                       self.env.user.name),
                subtype_xmlid='mail.mt_comment',
            )
            rec._ensure_placeholder_calendar()
            rec._notify_board_submission()

    def _notify_board_submission(self):
        """Schedule an activity for a Floor Recorder / Secretary."""
        self.ensure_one()
        floor_group = self.env.ref(
            'elksevent.group_event_officer', raise_if_not_found=False,
        )
        if not floor_group:
            return
        recorder = self.env['res.users'].search(
            [('group_ids', 'in', floor_group.id)], limit=1,
        )
        if not recorder:
            return
        self.activity_schedule(
            'mail.mail_activity_data_todo',
            user_id=recorder.id,
            summary=_("Event submitted for Board agenda: %s", self.name),
            note=_(
                "Event '%(name)s' on %(date)s for %(guests)s guests has been "
                "submitted for approval. Please add it to the next Board "
                "meeting agenda.\n\nCustomer: %(customer)s",
                name=self.name,
                date=self.x_event_date or 'N/A',
                guests=self.x_guest_count or 0,
                customer=self.x_customer_name or 'N/A',
            ),
        )

    def action_board_approve(self):
        """Board approval is FINAL — approve + book the event (no Floor vote)."""
        self._ensure_approver(
            'elksevent.group_event_officer', _("approve at the Board level"))
        for rec in self:
            if rec.x_approval_state != 'board':
                raise UserError(_("This event is not in the Board queue."))
            rec._finalize_event_approval()

    def action_board_reject(self):
        """Open the reject-reason wizard for a Board rejection."""
        self.ensure_one()
        self._ensure_approver(
            'elksevent.group_event_officer', _("reject at the Board level"))
        if self.x_approval_state != 'board':
            raise UserError(_("This event is not in the Board queue."))
        return self._open_reject_wizard('board')

    def _open_reject_wizard(self, stage):
        return {
            'type': 'ir.actions.act_window',
            'name': _("Reject Event"),
            'res_model': 'elks.event.reject.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_event_id': self.id,
                'default_reject_stage': stage,
            },
        }

    def _finalize_event_approval(self, notes=""):
        """Approve + book the event: set approved, move to the Booked stage,
        build checklist subtasks, promote the calendar, email the customer, and
        drop the insurance to-do. Board approval is final."""
        self.ensure_one()
        vals = {
            'x_approval_state': 'approved',
            'x_board_approval_date': fields.Date.context_today(self),
            'x_board_approved_by': self.env.uid,
        }
        if notes:
            vals['x_board_notes'] = notes
        booked_stage = self.env.ref(
            'elksevent.stage_booked', raise_if_not_found=False,
        )
        if booked_stage:
            vals['stage_id'] = booked_stage.id
        self.write(vals)
        self.message_post(
            body=_("<b>Board Approved &amp; Booked</b> on %(date)s.%(notes)s",
                   date=self.x_board_approval_date,
                   notes=(f"<br/>{notes}" if notes else "")),
            subtype_xmlid='mail.mt_comment',
        )
        # Auto-build the staff checklist subtasks on approval (best-effort)
        self._generate_checklist_subtasks(raise_if_missing=False)
        # Promote the tentative calendar entry to a confirmed booking
        self._promote_calendar()
        # Notify the customer
        self._send_event_mail('elksevent.tmpl_event_approved')
        # Remind the coordinator to purchase K&K rental insurance
        self._schedule_insurance_todo()

    def action_record_floor_approved(self, notes=""):
        """Back-compat shim for the retired Floor Vote wizard."""
        self._finalize_event_approval(notes)

    def _schedule_insurance_todo(self):
        """On final approval, drop a to-do to buy the K&K facility-rental
        insurance, due 4 days before the event. Skipped for the lodge's own
        (Elks) events and when the renter is bringing their own certificate."""
        self.ensure_one()
        if self.x_is_elks_event or self.x_insurance_by == 'renter':
            return
        settings = self._event_settings()
        url = (settings.x_insurance_program_url if settings else '') or (
            'https://insure.kandkinsurance.com/sites/ElksLodge/Pages/'
            'ELKSInEligibility.aspx')
        deadline = False
        if self.x_event_date:
            deadline = self.x_event_date - timedelta(days=4)
        try:
            self.activity_schedule(
                'mail.mail_activity_data_todo',
                date_deadline=deadline or None,
                user_id=self._after_action_user().id,
                summary=_("Purchase event rental insurance (K&K)"),
                note=_(
                    "Buy the facility-rental insurance for this event through "
                    "the Elks program. Due 4 days before the event.<br/>"
                    "<a href=\"%(url)s\" target=\"_blank\">Open the insurance "
                    "application</a>",
                    url=url),
            )
        except Exception as e:  # noqa: BLE001 - never block approval
            _logger.warning(
                "Insurance to-do failed for task %s: %s", self.id, e)

    def action_record_floor_rejected(self, notes=""):
        """Called by the reject wizard (board or floor) on a rejection."""
        self.ensure_one()
        vals = {
            'x_approval_state': 'rejected',
            'x_board_approval_date': fields.Date.context_today(self),
            'x_board_approved_by': self.env.uid,
        }
        if notes:
            vals['x_board_denial_reason'] = notes
        cancelled_stage = self.env.ref(
            'elksevent.stage_cancelled', raise_if_not_found=False,
        )
        if cancelled_stage:
            vals['stage_id'] = cancelled_stage.id
        self.write(vals)
        self.message_post(
            body=_("<b>Rejected</b> on %(date)s.%(reason)s",
                   date=self.x_board_approval_date,
                   reason=(f"<br/>Reason: {notes}" if notes else "")),
            subtype_xmlid='mail.mt_comment',
        )
        self._send_event_mail('elksevent.tmpl_event_denied')

    def action_reset_to_draft(self):
        """Reset a rejected event back to draft."""
        for rec in self:
            if rec.x_approval_state != 'rejected':
                raise UserError(_("Only rejected events can be reset."))
            rec.x_approval_state = 'draft'
            rec.message_post(
                body=_("Reset to <b>Draft</b> by %s.", self.env.user.name),
                subtype_xmlid='mail.mt_comment',
            )

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Event checklist → staff subtasks
    # HUMAN: Turns the checklist answers into a to-do list. Only the items
    #        that actually apply become sub-tasks for staff, pulled from the
    #        default checklist template. Runs on approval and on the button.
    # AI: _checklist_applicability maps item_code -> bool (MUST match
    #     CHECKLIST_ITEM_CODES). _generate_checklist_subtasks is idempotent via
    #     child x_checklist_code; allocated_hours set only if the field exists.
    # ──────────────────────────────────────────────────────────────────
    def _checklist_applicability(self):
        """Map each checklist item code to whether it applies to this event."""
        self.ensure_one()
        return {
            'tables': (self.x_need_tables or 0) > 0,
            'chairs': (self.x_need_chairs or 0) > 0,
            'podium': self.x_need_podium,
            'sound': self.x_need_sound,
            'microphone': self.x_need_microphone,
            'linen': self.x_need_linen,
            'insurance': self.x_need_insurance,
            'kitchen': self.x_use_kitchen,
            'garbage': self.x_need_garbage,
            'bars': (self.x_num_bars or 0) > 0,
            'beer_tub': self.x_need_beer_tub,
            'decorations': self.x_has_decorations,
            'decor_takedown': (
                self.x_has_decorations and self.x_decor_takedown_by == 'us'),
            'setup': self.x_setup_by == 'us',
            'takedown': self.x_takedown_by == 'us',
            # Clean-up always applies — every event needs cleanup assigned.
            'cleanup': True,
            'food_us': self.x_food_by_us or self.x_food_request,
            'food_catered': bool(self.x_caterer_id),
            'rooms': bool(self.x_room_booking_ids),
            'extras': bool(self.x_extras),
        }

    def _generate_checklist_subtasks(self, raise_if_missing=False):
        """Create subtasks for the applicable checklist items.

        Idempotent: an item already represented by a child task (matched on
        ``x_checklist_code``) is skipped, so re-running only adds new ones.
        """
        self.ensure_one()
        template = self.env['elks.event.checklist.template'].get_default_template()
        if not template:
            if raise_if_missing:
                raise UserError(_(
                    "No active checklist template found. Configure one under "
                    "Events → Configuration → Checklist Templates."
                ))
            return self.env['project.task']

        applicability = self._checklist_applicability()
        existing_codes = set(
            c for c in self.child_ids.mapped('x_checklist_code') if c
        )
        Task = self.env['project.task']
        created = Task
        has_alloc = 'allocated_hours' in Task._fields
        # Default deadline for generated subtasks = the event date, so they're
        # scheduled out of the box (the coordinator can adjust per task).
        deadline = False
        if self.x_event_date and 'date_deadline' in Task._fields:
            if Task._fields['date_deadline'].type == 'datetime':
                from datetime import datetime, time as _dtime
                # Local noon on the event date, stored in UTC.
                naive = datetime.combine(self.x_event_date, _dtime(12, 0))
                tz = pytz.timezone(self.env.user.tz or 'UTC')
                deadline = tz.localize(naive).astimezone(
                    pytz.utc).replace(tzinfo=None)
            else:
                deadline = self.x_event_date
        item_labels = dict(
            self.env['elks.event.checklist.template.line']
            ._fields['item_code'].selection
        )
        for line in template.line_ids.filtered('create_subtask'):
            code = line.item_code
            if not applicability.get(code) or code in existing_codes:
                continue
            vals = {
                'name': line.name or item_labels.get(code, code),
                'parent_id': self.id,
                'project_id': self.project_id.id,
                'x_checklist_code': code,
            }
            if line.default_user_id:
                vals['user_ids'] = [(6, 0, [line.default_user_id.id])]
            if has_alloc and line.planned_hours:
                vals['allocated_hours'] = line.planned_hours
            if deadline:
                vals['date_deadline'] = deadline
            created |= Task.create(vals)
            existing_codes.add(code)

        if created:
            self.message_post(
                body=_("Generated %s checklist subtask(s).", len(created)),
                subtype_xmlid='mail.mt_note',
            )
        # Checklist kickoff: alert the in-use department leads with their
        # call-out (best-effort; never block checklist generation).
        try:
            self._kickoff_department_callouts()
        except Exception:  # noqa: BLE001
            pass
        return created

    def action_generate_checklist_tasks(self):
        """Button: generate the checklist subtasks for this event."""
        self.ensure_one()
        created = self._generate_checklist_subtasks(raise_if_missing=True)
        if not created:
            self.message_post(
                body=_(
                    "No new checklist subtasks to add — either none apply yet "
                    "or they already exist."
                ),
                subtype_xmlid='mail.mt_note',
            )
        return True

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Quote engine (sale.order) — staff-only itemized quote
    # HUMAN: Builds the behind-the-scenes quote: a line per room, plus
    #        bartenders (one per N guests), catering (per plate), and the
    #        lodge's default products. Member discount shows as a % on each
    #        line. The coordinator-fee button adds that line and nudges staff
    #        if labor hours are missing. The customer never sees this quote.
    # AI: _sync_quote_lines clears x_event_source lines then rebuilds them,
    #     preserving manual lines. Uses _event_settings() for products/ratios.
    #     x_quote_total = SO.amount_untaxed; invoices read from it.
    # ──────────────────────────────────────────────────────────────────
    def _event_settings(self):
        return self.env['elks.lodge.settings'].sudo().search([], limit=1)

    @api.depends('x_sale_order_id.amount_untaxed')
    def _compute_quote_total(self):
        for rec in self:
            rec.x_quote_total = (
                rec.x_sale_order_id.amount_untaxed
                if rec.x_sale_order_id else 0.0
            )

    def action_open_quote(self):
        """Create the quote if needed, then open it (staff-only)."""
        self.ensure_one()
        if self.x_is_elks_event:
            raise UserError(_(
                "Elks Events are not billed, so they have no quote."))
        if not self.x_sale_order_id:
            self._create_event_quote()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Event Quote"),
            'res_model': 'sale.order',
            'res_id': self.x_sale_order_id.id,
            'view_mode': 'form',
        }

    def _create_event_quote(self):
        self.ensure_one()
        partner = self._get_event_partner()
        so = self.env['sale.order'].create({
            'partner_id': partner.id,
            'x_event_task_id': self.id,
            'origin': self.name,
        })
        self.x_sale_order_id = so
        self._sync_quote_lines()
        return so

    def action_build_quote(self):
        """Create the quote if needed and (re)build the automatic lines."""
        self.ensure_one()
        if self.x_is_elks_event:
            raise UserError(_(
                "Elks Events are not billed, so they have no quote."))
        if not self.x_sale_order_id:
            self._create_event_quote()
        else:
            self._sync_quote_lines()
        return self.action_open_quote()

    def _sync_quote_lines(self):
        """Rebuild the customer quote as ONE 'Event Rental' line (+ tax).

        The customer only ever sees a single 'Event Rental' line and a tax
        line. That price is built from the event's own tabs — Rooms + Event
        Costs + the Coordinator fee — net of the member / Celebration-of-Life
        discount. Items flagged non-taxable (on Event Costs) go on a second,
        untaxed 'Event Rental' line so tax lands on taxable items only; a note
        line spells that out. Auto lines carry x_event_source so a rebuild only
        touches them (manual lines are preserved). The full breakdown lives on
        the event's Financials tab, not on the order.
        """
        self.ensure_one()
        so = self.x_sale_order_id
        if not so:
            return
        settings = self._event_settings()
        so.order_line.filtered('x_event_source').unlink()
        SOL = self.env['sale.order.line']

        rental_product = settings.x_facility_product_id if settings else False
        if not rental_product:
            raise UserError(_(
                "Set the 'Facility (Invoice) Product' in Lodge Settings — it's "
                "used as the single 'Event Rental' line on the quote."))

        # NOTE: the coordinator fee is NOT auto-seeded here. Building a quote
        # must never change it — some events are unsponsored ($0 fee). The fee
        # only changes when staff press the "Coordinator Fee" button
        # (action_add_coordinator_fee), which sets it to the suggested amount.

        # The member / Celebration-of-Life discount applies to the ROOM ONLY
        # (never event costs or the coordinator fee). Kept in sync with
        # _compute_financials via the shared helper.
        rooms_net, _room_disc = self._event_room_discount()

        # Event Costs = customer charges, billed at FULL (not discounted).
        ec_taxable = 0.0
        ec_nontax = 0.0
        for c in self.x_cost_line_ids:
            amt = (c.total or 0.0)
            if c.x_taxable:
                ec_taxable += amt
            else:
                ec_nontax += amt

        # Coordinator fee is NOT discounted (built on room retail cost).
        coord = (self.x_coordinator_fee or 0.0)

        # Taxable = GOODS only: rooms + Event-Cost items flagged taxable.
        # Services (coordinator fee + anything marked non-taxable) are exempt.
        taxable_total = rooms_net + ec_taxable
        nontax_total = ec_nontax + coord
        tax_ids = (settings.x_event_tax_id.ids
                   if settings and settings.x_event_tax_id else [])

        # Taxable Event Rental line (always present, even if $0 with no extras).
        SOL.create({
            'order_id': so.id,
            'product_id': rental_product.id,
            'name': _("Event Rental"),
            'product_uom_qty': 1,
            'price_unit': taxable_total,
            'tax_ids': [(6, 0, tax_ids)],
            'x_event_source': 'event_rental',
        })
        # Non-taxable portion, only when there is one.
        if nontax_total:
            SOL.create({
                'order_id': so.id,
                'product_id': rental_product.id,
                'name': _("Event Rental - non-taxable services"),
                'product_uom_qty': 1,
                'price_unit': nontax_total,
                'tax_ids': [(6, 0, [])],
                'x_event_source': 'event_rental_nt',
            })
        # Disclaimer note under the totals.
        SOL.create({
            'order_id': so.id,
            'display_type': 'line_note',
            'name': _("Taxes applied to taxable items only."),
            'x_event_source': 'tax_note',
        })

        # (Estimated labor hours now come from the planning inputs on the
        # Labor section — see _compute_est_hours — not seeded here.)

    def action_add_coordinator_fee(self):
        """Reset the coordinator fee to the suggested (retail-based) amount.

        Clears the 'Custom fee' flag so the fee auto-tracks the room rate
        again, then rebuilds the quote.
        """
        self.ensure_one()
        self.x_coordinator_fee_manual = False
        self.x_coordinator_fee = self._coordinator_auto_value()
        if self.x_sale_order_id:
            self._sync_quote_lines()
        # Nudge if labor isn't captured when the lodge does setup/takedown
        if self.x_setup_by == 'us' or self.x_takedown_by == 'us':
            labor = self.x_cost_line_ids.filtered(
                lambda c: c.cost_type_id.is_labor)
            if not labor:
                self.message_post(
                    body=_(
                        "<b>Reminder:</b> the lodge is doing setup/takedown "
                        "but no event-staff labor lines are entered. Add them "
                        "so labor is captured in costs."),
                    subtype_xmlid='mail.mt_note',
                )
        self.message_post(
            body=_("Coordinator fee set to the suggested $%(fee).2f",
                   fee=self.x_coordinator_fee),
            subtype_xmlid='mail.mt_note',
        )
        return True

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Labor hours — actuals from the timeclock + true-up
    # HUMAN: Pull the hours actually worked (from the timeclock, tagged to
    #        this event) and compare them to what we charged, so the final
    #        bill can be brought down (or up) to match reality.
    # AI: _compute_actual_hours is non-stored — it re-reads hr.attendance on
    #     access, grouped by x_event_role. action_trueup_labor_hours copies
    #     actuals into the est fields and rebuilds the quote.
    # ──────────────────────────────────────────────────────────────────
    @api.depends('x_est_event_hours', 'x_est_cleaning_hours',
                 'x_est_bartender_hours',
                 'x_cost_line_ids.x_line_cogs', 'x_cost_line_ids.x_auto_source',
                 'x_attendance_ids.worked_hours', 'x_attendance_ids.x_event_role',
                 'x_attendance_ids.employee_id')
    def _compute_actual_hours(self):
        settings = self.env['elks.lodge.settings'].sudo().search([], limit=1)
        # Per-hour COST rates from the Lodge Settings (fallback to the old
        # per-hour products if a rate is left blank).
        ev_rate = (settings.x_event_staff_rate
                   or (settings.x_event_labor_product_id.list_price
                       if settings.x_event_labor_product_id else 0.0)
                   ) if settings else 0.0
        cl_rate = (settings.x_custodial_rate
                   or (settings.x_cleaning_product_id.list_price
                       if settings.x_cleaning_product_id else 0.0)
                   ) if settings else 0.0
        bt_rate = (settings.x_bartender_rate
                   or (settings.x_bartender_product_id.list_price
                       if settings.x_bartender_product_id else 0.0)
                   ) if settings else 0.0
        # Representative kitchen cost rate for ACTUAL hours (the timeclock only
        # carries a single 'kitchen' role, not cook vs support).
        kt_rate = (settings.x_cook_rate or settings.x_kitchen_rate
                   or 0.0) if settings else 0.0
        for rec in self:
            atts = rec.x_attendance_ids
            ev = sum(atts.filtered(
                lambda a: a.x_event_role == 'event').mapped('worked_hours'))
            cl = sum(atts.filtered(
                lambda a: a.x_event_role == 'custodial').mapped('worked_hours'))
            bt = sum(atts.filtered(
                lambda a: a.x_event_role == 'bartender').mapped('worked_hours'))
            kt = sum(atts.filtered(
                lambda a: a.x_event_role == 'kitchen').mapped('worked_hours'))
            rec.x_actual_event_hours = ev
            rec.x_actual_cleaning_hours = cl
            rec.x_actual_bartender_hours = bt
            rec.x_actual_kitchen_hours = kt
            rec.x_actual_total_hours = ev + cl + bt + kt
            rec.x_event_hours_variance = (rec.x_est_event_hours or 0.0) - ev
            rec.x_cleaning_hours_variance = (rec.x_est_cleaning_hours or 0.0) - cl
            # Estimated labor = the labor already itemized in COGS (event + bar
            # + cooks + support + cleaning), so it ties to the P&L exactly.
            rec.x_est_labor_cost = sum(rec.x_cost_line_ids.filtered(
                lambda l: (l.x_auto_source or '').startswith('labor')
            ).mapped('x_line_cogs'))
            # Actual labor COST counts PAID (non-volunteer) hours only, to match
            # the P&L — volunteers work for free.
            rec.x_actual_labor_cost = sum(rec._paid_labor_by_category().values())

    # ── Auto-build service cost lines from the plan + rates ─────────────
    _LABOR_PLAN_KEYS = frozenset((
        'x_plan_setup_hours', 'x_plan_event_hours', 'x_plan_teardown_hours',
        'x_plan_bar_hours', 'x_plan_cleaning_hours', 'x_plan_kitchen_hours',
        'x_bartender_count', 'x_num_bars',
        'x_event_workers_use', 'x_event_worker_count', 'x_event_worker_hours',
        'x_cleaning_use', 'x_cleaner_count', 'x_cleaner_hours',
        'x_cook_count', 'x_cook_hours',
        'x_kitchen_support_count', 'x_kitchen_support_hours',
        'x_est_event_hours', 'x_est_bartender_hours', 'x_est_cleaning_hours',
        'x_est_cook_hours', 'x_est_kitchen_support_hours',
        'x_bar_gratuity', 'x_kitchen_gratuity',
        'x_marketing_signage', 'x_exclusive_use',
        'x_bar_use', 'x_food_request', 'x_is_elks_event',
        'x_insurance_by'))

    def _sync_labor_cost_lines(self):
        """(Re)build the auto service cost lines from the planned hours and the
        Lodge Settings rates: charge = hours x rate x (1 + markup) + service
        fee; COGS = hours x rate. Only lines tagged x_auto_source are touched,
        so hand-added Event Costs lines are preserved.
        """
        settings = self._event_settings()
        Line = self.env['elks.event.cost.line']
        Type = self.env['elks.event.cost.type']
        cache = {}

        def tid(code):
            if code not in cache:
                t = Type.search([('code', '=', code)], limit=1)
                cache[code] = t.id if t else False
            return cache[code]

        for rec in self:
            if not rec.x_is_event or not settings:
                continue
            rec.x_cost_line_ids.filtered('x_auto_source').unlink()
            markup = 1.0 + ((settings.x_labor_markup_pct or 0.0) / 100.0)
            # (include, code, name, hours, rate, service_fee, source). Bar and
            # Catering lines are gated by their checkbox (Bar Use / Food-Catering
            # Request); Event Staff and Cleaning follow the planned hours.
            specs = [
                (rec.x_event_workers_use, 'event_service',
                 _("Event Service (labor)"),
                 rec.x_est_event_hours, settings.x_event_staff_rate,
                 settings.x_event_service_fee, 'labor_event'),
                (rec.x_bar_use, 'bar_service', _("Bar Service (labor)"),
                 rec.x_est_bartender_hours, settings.x_bartender_rate,
                 settings.x_bar_service_fee, 'labor_bar'),
                (rec.x_cleaning_use, 'cleaning_service',
                 _("Cleaning Service (labor)"),
                 rec.x_est_cleaning_hours, settings.x_custodial_rate,
                 settings.x_custodial_service_fee, 'labor_clean'),
                # Kitchen split into cooks + support, each priced at its own
                # rate (Lodge Settings). The one-time kitchen service fee rides
                # the cook line (mirrors how the bar fee rides the bar line).
                (rec.x_food_request, 'catering_service',
                 _("Catering - Cooks (labor)"),
                 rec.x_est_cook_hours, settings.x_cook_rate,
                 settings.x_kitchen_service_fee, 'labor_cook'),
                (rec.x_food_request, 'catering_service',
                 _("Catering - Kitchen Support (labor)"),
                 rec.x_est_kitchen_support_hours,
                 settings.x_kitchen_support_rate, 0.0, 'labor_kitchen_support'),
            ]
            for include, code, name, hours, rate, fee, src in specs:
                if not include:
                    continue
                type_id = tid(code)
                if not type_id:
                    continue
                hours = hours or 0.0
                rate = rate or 0.0
                fee = fee or 0.0
                cogs = hours * rate
                charge = cogs * markup + fee
                if not charge and not cogs:
                    continue
                Line.create({
                    'event_id': rec.id,
                    'cost_type_id': type_id,
                    'name': name,
                    'quantity': 1,
                    'unit_cost': round(charge, 2),
                    'x_line_cogs': round(cogs, 2),
                    'x_taxable': False,
                    'x_auto_source': src,
                })
            # A CERTIFIED department call-out roster supersedes the plan
            # estimate for that department's labor COGS: the plan line keeps its
            # billed charge (unit_cost) but hands its COGS to a dedicated
            # "… Staff (certified roster)" line carrying the manager's committed
            # cost (hours x rate). Single COGS source, so no double counting;
            # gratuity is handled separately via x_*_gratuity.
            dept_srcs = {'bar': ('labor_bar',),
                         'kitchen': ('labor_cook', 'labor_kitchen_support'),
                         'custodial': ('labor_clean',)}
            dept_code = {'bar': 'bar_service',
                         'kitchen': 'catering_service',
                         'custodial': 'cleaning_service'}
            for co in rec.x_callout_ids.filtered(
                    lambda c: c.state == 'certified'):
                srcs = dept_srcs.get(co.department)
                if not srcs:
                    continue
                for pl in rec.x_cost_line_ids.filtered(
                        lambda x: x.x_auto_source in srcs):
                    pl.x_line_cogs = 0.0
                ctype = tid(dept_code.get(co.department))
                if ctype and co.cost_total:
                    Line.create({
                        'event_id': rec.id,
                        'cost_type_id': ctype,
                        'name': _("%s Staff (certified roster)")
                        % co.department_label,
                        'quantity': 1,
                        'unit_cost': 0.0,
                        'x_line_cogs': round(co.cost_total, 2),
                        'x_taxable': False,
                        'x_auto_source': 'roster_%s' % co.department,
                    })
                # Gratuity is a pass-through: charged to the customer (revenue)
                # and paid out to staff (COGS), so it nets to zero for the lodge
                # but appears on both sides of the books.
                gtype = tid('gratuity') or tid('other')
                if gtype and co.gratuity:
                    Line.create({
                        'event_id': rec.id,
                        'cost_type_id': gtype,
                        'name': _("%s Gratuity (pass-through)")
                        % co.department_label,
                        'quantity': 1,
                        'unit_cost': round(co.gratuity, 2),
                        'x_line_cogs': round(co.gratuity, 2),
                        'x_taxable': False,
                        'x_auto_source': 'roster_grat_%s' % co.department,
                    })

            # Add-on fees toggled by checkbox (fee from Lodge Settings).
            for flag, code, fee, name, src in (
                    (rec.x_marketing_signage, 'marketing_signage',
                     settings.x_marketing_signage_fee,
                     _("Marketing Signage Use"), 'addon_signage'),
                    (rec.x_exclusive_use, 'exclusive_use',
                     settings.x_exclusive_use_fee,
                     _("Exclusive Use"), 'addon_exclusive')):
                type_id = tid(code)
                if flag and type_id:
                    Line.create({
                        'event_id': rec.id,
                        'cost_type_id': type_id,
                        'name': name,
                        'quantity': 1,
                        'unit_cost': round(fee or 0.0, 2),
                        'x_line_cogs': 0.0,
                        'x_taxable': False,
                        'x_auto_source': src,
                    })

            # Insurance — the lodge charges a general liability-insurance fee on
            # every event it hosts for someone else (NOT an Elks/lodge event)
            # WHEN the lodge is providing the coverage (Insurance Provided By =
            # Lodge). If the renter brings their own certificate, no charge.
            # Seeded from Lodge Settings but managed idempotently (NOT tagged
            # x_auto_source) so a per-event amount the coordinator types in
            # survives later cost-line rebuilds.
            ins_type = tid('insurance')
            if ins_type:
                ins_lines = rec.x_cost_line_ids.filtered(
                    lambda c: c.cost_type_id.id == ins_type)
                lodge_covers = (
                    not rec.x_is_elks_event
                    and rec.x_insurance_by == 'lodge')
                if not lodge_covers:
                    ins_lines.unlink()
                elif not ins_lines:
                    ins_fee = settings.x_event_insurance_fee or 0.0
                    if ins_fee:
                        Line.create({
                            'event_id': rec.id,
                            'cost_type_id': ins_type,
                            'name': _("Event Insurance"),
                            'quantity': 1,
                            'unit_cost': round(ins_fee, 2),
                            'x_line_cogs': 0.0,
                            'x_taxable': False,
                        })

            # Gratuity — a pass-through: charged to the customer AND paid out to
            # staff, so Amount = COGS = the pool (net-zero to the lodge).
            grat_type = tid('gratuity')
            for amount, name, src in (
                    (rec.x_bar_gratuity, _("Gratuity - Bar"), 'grat_bar'),
                    (rec.x_kitchen_gratuity, _("Gratuity - Kitchen"),
                     'grat_kitchen')):
                if grat_type and amount:
                    Line.create({
                        'event_id': rec.id,
                        'cost_type_id': grat_type,
                        'name': name,
                        'quantity': 1,
                        'unit_cost': round(amount, 2),
                        'x_line_cogs': round(amount, 2),
                        'x_taxable': False,
                        'x_auto_source': src,
                    })

    def action_distribute_gratuity(self):
        """Split each gratuity pool across that role's timeclock shifts, by
        hours, onto the attendance records for payroll."""
        self.ensure_one()

        def _split(pool, role):
            atts = self.x_attendance_ids.filtered(
                lambda a: a.x_event_role == role)
            total = sum(atts.mapped('worked_hours'))
            if pool and total:
                for a in atts:
                    a.sudo().x_gratuity_share = round(
                        pool * (a.worked_hours / total), 2)
            else:
                atts.sudo().write({'x_gratuity_share': 0.0})

        _split(self.x_bar_gratuity or 0.0, 'bartender')
        _split(self.x_kitchen_gratuity or 0.0, 'kitchen')
        self.message_post(
            body=_("Gratuity distributed to shifts — bar $%(b).2f, "
                   "kitchen $%(k).2f.",
                   b=self.x_bar_gratuity or 0.0,
                   k=self.x_kitchen_gratuity or 0.0),
            subtype_xmlid='mail.mt_note')
        return True

    def action_pay_coordinator(self):
        """Post the coordinator fee to the tagged coordinator's timecard as a
        flat payout — no clocked hours required (coordinators are fee-based).

        Reuses an existing event punch for that employee if there is one;
        otherwise creates a zero-duration attendance tagged to this event so the
        amount rides their timecard. Idempotent: re-running moves the amount, it
        does not stack.
        """
        self.ensure_one()
        emp = self.x_coordinator_employee_id
        fee = self.x_coordinator_fee or 0.0
        if not emp:
            raise UserError(_("Tag an Event Coordinator (employee) first."))
        if fee <= 0:
            raise UserError(_("There is no coordinator fee to pay."))
        Att = self.env['hr.attendance'].sudo()
        atts = self.x_attendance_ids.filtered(
            lambda a: a.employee_id == emp)
        # Clear any prior coordinator payout on this event so we never stack.
        self.x_attendance_ids.filtered(
            lambda a: a.x_coordinator_fee_share).sudo().write(
            {'x_coordinator_fee_share': 0.0})
        if atts:
            target = atts[0]
        else:
            when = self.date_deadline or fields.Datetime.now()
            target = Att.create({
                'employee_id': emp.id,
                'check_in': when,
                'check_out': when,
                'x_event_id': self.id,
                'x_event_role': 'event',
            })
        target.write({'x_coordinator_fee_share': round(fee, 2)})
        self.x_coordinator_fee_paid = True
        self.message_post(
            body=_("Coordinator fee $%(f).2f posted to %(name)s's timecard.",
                   f=fee, name=emp.name),
            subtype_xmlid='mail.mt_note')
        return True

    # ── Department call-outs (Bar / Kitchen / Custodial) ────────────────
    _CALLOUT_DEPTS = {
        'bar': ('x_bar_manager_id', 'Bar'),
        'kitchen': ('x_kitchen_manager_id', 'Kitchen'),
        'custodial': ('x_custodial_manager_id', 'Custodial'),
    }

    def _email_callout(self, dept):
        """Render the department call-out PDF for `dept` and email it to that
        department's manager (the employee tagged with the role, or a per-event
        override). Also usable to just attach the PDF."""
        self.ensure_one()
        field, label = self._CALLOUT_DEPTS[dept]
        # Manager comes from the employee tagged with this department role
        # (Event Bar/Kitchen/Custodial Manager on their HR record), or a
        # per-event override.
        partner = self._callout_manager(dept)
        if not partner:
            raise UserError(_(
                "No %s Manager found. On an employee's HR record (Work tab), "
                "tick 'Event %s Manager' so they receive this call-out.",
                label, label))
        if not partner.email:
            raise UserError(_(
                "%(name)s is the %(dept)s Manager but has no email. Add an "
                "email (or a Related User) on their employee record so the "
                "call-out can be sent.", name=partner.name, dept=label))
        pdf, _dummy = self.env['ir.actions.report']._render_qweb_pdf(
            'elksevent.report_event_callout_%s' % dept, self.ids)
        fname = '%s Call-Out - %s.pdf' % (label, self.name or 'Event')
        attachment = self.env['ir.attachment'].create({
            'name': fname,
            'type': 'binary',
            'datas': base64.b64encode(pdf),
            'res_model': 'project.task',
            'res_id': self.id,
            'mimetype': 'application/pdf',
        })
        self.env['mail.mail'].sudo().create({
            'subject': _('%(dept)s Call-Out - %(name)s',
                         dept=label, name=self.name or 'Event'),
            'body_html': _(
                '<p>Hello %(mgr)s,</p><p>Please review the attached %(dept)s '
                'call-out / quote request for <strong>%(name)s</strong> and '
                'reply with your staff assignments and quote.</p>'
                '<p>Thank you.</p>',
                mgr=partner.name, dept=label, name=self.name or 'the event'),
            'email_to': partner.email,
            'attachment_ids': [(4, attachment.id)],
        }).send()
        self.message_post(
            body=_("%(dept)s call-out emailed to %(mgr)s.",
                   dept=label, mgr=partner.name),
            subtype_xmlid='mail.mt_note')
        return True

    def action_email_bar_callout(self):
        return self._email_callout('bar')

    def action_email_kitchen_callout(self):
        return self._email_callout('kitchen')

    def action_email_custodial_callout(self):
        return self._email_callout('custodial')

    # ── Digital call-outs (portal) ─────────────────────────────────────
    def _send_digital_callout(self, dept):
        """Create/refresh the department call-out, mark it Sent, and email the
        manager a link to review + certify it in their portal (/my)."""
        self.ensure_one()
        Callout = self.env['elks.event.callout']
        co = Callout._get_or_create(self, dept)
        mgr = co.manager_partner_id or self._callout_manager(dept)
        if not mgr:
            raise UserError(_(
                "Set the %s Manager (on this event or in Lodge Settings) "
                "before sending the call-out.", co.department_label))
        # Refresh the estimate from the event, then mark as sent.
        co._seed_from_event()
        co.write({'manager_partner_id': mgr.id, 'state': 'sent'})
        base = self.get_base_url()
        link = '%s/my/callout/%s' % (base, co.id)
        if mgr.email:
            self.env['mail.mail'].sudo().create({
                'subject': _('%(dept)s Call-Out to review - %(name)s',
                             dept=co.department_label, name=self.name or 'Event'),
                'body_html': _(
                    '<p>Hello %(mgr)s,</p><p>Please review the %(dept)s '
                    'call-out for <strong>%(name)s</strong>, adjust the staff '
                    'counts/hours as needed, add any notes, and certify it.</p>'
                    '<p><a href="%(link)s">Open the call-out</a></p>',
                    mgr=mgr.name, dept=co.department_label,
                    name=self.name or 'the event', link=link),
                'email_to': mgr.email,
            }).send()
        self.message_post(
            body=_("%(dept)s digital call-out sent to %(mgr)s for review.",
                   dept=co.department_label, mgr=mgr.name),
            subtype_xmlid='mail.mt_note')
        return co

    def _remind_pending_callouts(self):
        """Bump every department manager whose call-out is still awaiting
        certification (state 'sent')."""
        self.ensure_one()
        pending = self.env['elks.event.callout'].sudo().search([
            ('event_id', '=', self.id), ('state', '=', 'sent')])
        for co in pending:
            co._send_reminder()
        return len(pending)

    def action_remind_pending_callouts(self):
        """Button: remind the managers who haven't certified their call-out."""
        self.ensure_one()
        n = self._remind_pending_callouts()
        if not n:
            self.message_post(
                body=_("No pending call-outs to remind — all are certified or "
                       "none have been sent yet."),
                subtype_xmlid='mail.mt_note')
        return True

    def _bookkeeper_pl_rows(self):
        """Revenue vs cost by P&L category for the bookkeeper report. Revenue
        rows reconcile to x_total_billed (pre-tax); cost rows to x_total_costs."""
        self.ensure_one()
        rev = {'catering': 0.0, 'bar': 0.0, 'event': 0.0,
               'cleaning': 0.0, 'other': 0.0, 'coordinator': 0.0}
        for c in self.x_cost_line_ids:
            cat = c.cost_type_id.category
            rev[cat if cat in rev else 'other'] += (c.total or 0.0)
        po_total = sum(self.x_purchase_order_ids.filtered(
            lambda p: p.state != 'cancel').mapped('amount_total'))
        rows = [
            {'name': 'Room Rental', 'revenue': self.x_room_net or 0.0,
             'cogs': 0.0},
            {'name': 'Catering', 'revenue': rev['catering'],
             'cogs': self.x_cat_catering or 0.0},
            {'name': 'Bar', 'revenue': rev['bar'],
             'cogs': self.x_cat_bar or 0.0},
            {'name': 'Event Services', 'revenue': rev['event'],
             'cogs': self.x_cat_event or 0.0},
            {'name': 'Cleaning', 'revenue': rev['cleaning'],
             'cogs': self.x_cat_cleaning or 0.0},
            {'name': 'Coordinator Fee',
             'revenue': (self.x_coordinator_fee or 0.0) + rev['coordinator'],
             'cogs': 0.0},
            {'name': 'Other / Gratuity', 'revenue': rev['other'],
             'cogs': self.x_cat_other or 0.0},
            {'name': 'Vendor Purchases (POs)', 'revenue': 0.0,
             'cogs': po_total},
        ]
        for r in rows:
            r['net'] = r['revenue'] - r['cogs']
        return rows

    def action_print_bookkeeper_report(self):
        self.ensure_one()
        return self.env.ref(
            'elksevent.action_report_event_bookkeeper').report_action(self)

    def action_send_bar_callout(self):
        self._send_digital_callout('bar')

    def action_send_kitchen_callout(self):
        self._send_digital_callout('kitchen')

    def action_send_custodial_callout(self):
        self._send_digital_callout('custodial')

    def action_build_labor_costs(self):
        """Button: rebuild the auto service cost lines and refresh the quote."""
        self.ensure_one()
        self._sync_labor_cost_lines()
        if self.x_sale_order_id:
            self._sync_quote_lines()
        self.message_post(
            body=_("Rebuilt service cost lines from the plan and rates."),
            subtype_xmlid='mail.mt_note')
        return True

    def _get_pl_report_data(self):
        """Build the Event P&L data structure for `self` (a set of events).

        Shared by the date-range P&L wizard report and the per-event Print
        report so both show identical numbers. Expenses = COGS by P&L category
        (labor already inside these) + purchase orders; income = the itemized
        quote total; profit = the event's net income. Mirrors the Financials
        tab and avoids double-counting labor / POs.
        """
        event_data = []
        grand_income = 0.0
        grand_costs = 0.0
        for evt in self:
            room_lines = [{
                'name': rb.room_id.name,
                'rate': rb.rate,
                'cleaning': rb.cleaning_fee,
                'service': rb.service_fee,
                'subtotal': rb.subtotal,
            } for rb in evt.x_room_booking_ids]

            cogs_cats = [
                ('Catering', evt.x_cat_catering or 0.0),
                ('Bar', evt.x_cat_bar or 0.0),
                ('Event Services', evt.x_cat_event or 0.0),
                ('Cleaning', evt.x_cat_cleaning or 0.0),
                ('Other / Gratuity', evt.x_cat_other or 0.0),
            ]
            cogs_cats = [(lbl, amt) for lbl, amt in cogs_cats if amt]

            # Itemized COGS = each non-labor cost line's COGS (goods: linens,
            # supplies, gratuity pass-through) + actual PAID labor per role.
            # This mirrors the income lines so "Linens" appears on both sides.
            cost_items = []
            for cl in evt.x_cost_line_ids:
                if (cl.x_auto_source or '').startswith('labor'):
                    continue  # estimated labor is the billing basis, not a cost
                if cl.x_line_cogs:
                    cost_items.append({
                        'name': cl.name or (cl.cost_type_id.name or 'Cost'),
                        'cogs': cl.x_line_cogs,
                    })
            _labor_labels = {
                'event': 'Event Service - paid labor',
                'bar': 'Bar Service - paid labor',
                'catering': 'Catering - paid labor',
                'cleaning': 'Cleaning Service - paid labor',
            }
            for cat, amt in evt._paid_labor_by_category().items():
                if amt:
                    cost_items.append({'name': _labor_labels[cat], 'cogs': amt})

            po_lines = [{
                'name': po.name,
                'origin': po.origin or '',
                'total': po.amount_total,
            } for po in evt.x_purchase_order_ids.filtered(
                lambda p: p.state != 'cancel')]

            # Every Event Cost line is a customer CHARGE (revenue) — show them
            # all on the income side so the P&L reconciles to the billed total.
            charge_lines = [{
                'name': cl.name or (cl.cost_type_id.name or 'Event Cost'),
                'total': cl.total or 0.0,
            } for cl in evt.x_cost_line_ids if cl.total]
            eventcosts_total = evt.x_eventcosts_total or 0.0

            room_income = evt.x_room_income or 0.0
            coordinator = evt.x_coordinator_fee or 0.0
            total_income = evt.x_total_billed or (
                room_income + eventcosts_total + coordinator)
            # Gross of any member / Celebration-of-Life discount; the discount
            # line is the difference so income always ties to the billed total.
            gross_income = room_income + eventcosts_total + coordinator
            discount = round(gross_income - total_income, 2)
            _reason_labels = {
                'member': 'Member', 'nonprofit': 'Non-Profit',
                'officer_board': 'Officer / Board Approved', 'other': 'Other',
            }
            if evt.x_discount_reason:
                # Show "(50%)" for a percent discount; the dollar amount is
                # already on the line, so an amount-type discount needs no
                # extra qualifier.
                qualifier = (
                    ' (%.0f%%)' % (evt.x_discount_pct or 0.0)
                    if evt.x_discount_type == 'percent' else '')
                discount_label = 'Less: %s discount%s' % (
                    _reason_labels.get(evt.x_discount_reason, 'Discount'),
                    qualifier,
                )
            elif discount:
                discount_label = 'Less: Celebration of Life (room waived)'
            else:
                discount_label = 'Less: Discount'

            cogs_total = evt.x_cogs or 0.0
            po_total = sum(p['total'] for p in po_lines)
            total_expense = evt.x_total_costs or (cogs_total + po_total)
            profit = (evt.x_net_income if evt.x_total_billed
                      else total_income - total_expense)

            grand_income += total_income
            grand_costs += total_expense

            event_data.append({
                'event': evt,
                'room_lines': room_lines,
                'charge_lines': charge_lines,
                'eventcosts_total': eventcosts_total,
                'cogs_cats': cogs_cats,
                'cost_items': cost_items,
                'cogs_total': cogs_total,
                'po_lines': po_lines,
                'room_income': room_income,
                'coordinator_fee': coordinator,
                'gross_income': gross_income,
                'discount': discount,
                'discount_label': discount_label,
                'total_income': total_income,
                'tax': evt.x_event_tax_amount or 0.0,
                'total_costs': cogs_total,
                'po_total': po_total,
                'total_expense': total_expense,
                'profit': profit,
                'is_member': evt.x_is_member,
                'ubi_room_income': evt.x_ubi_room_income or 0.0,
                'ubi_tax_reserve': evt.x_ubi_tax_reserve or 0.0,
            })

        return {
            'events': event_data,
            'event_count': len(event_data),
            'grand_income': grand_income,
            'grand_costs': grand_costs,
            'grand_profit': grand_income - grand_costs,
        }

    def action_open_add_hours(self):
        """Open the wizard to attach a worker's recorded timeclock shifts."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Add Timeclock Hours"),
            'res_model': 'elks.event.add.hours.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_event_id': self.id},
        }

    def action_trueup_labor_hours(self):
        """True-up: set estimated hours to what the timeclock actually shows
        (per role), so the actual labor cost flows into the event's P&L costs.
        """
        self.ensure_one()
        self.x_est_event_hours = self.x_actual_event_hours
        self.x_est_cleaning_hours = self.x_actual_cleaning_hours
        self.x_est_bartender_hours = self.x_actual_bartender_hours
        self.message_post(
            body=_(
                "Labor trued-up to actual hours — event %(ev).1f h, "
                "bartender %(bt).1f h, cleaning %(cl).1f h "
                "(labor cost $%(cost).2f rolled into the P&L).",
                ev=self.x_actual_event_hours,
                bt=self.x_actual_bartender_hours,
                cl=self.x_actual_cleaning_hours,
                cost=self.x_actual_labor_cost),
            subtype_xmlid='mail.mt_note',
        )
        return True

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Calendar booking (placeholder until approved)
    # HUMAN: Puts a tentative (greyed) hold on the lodge calendar when the
    #        event is submitted, then makes it a real booking once approved.
    # AI: _ensure_placeholder_calendar (show_as='free', 'TENTATIVE:' name) ->
    #     _promote_calendar (show_as='busy'). Window from x_event_date or
    #     date_deadline + start/end float times. All sudo, wrapped so calendar
    #     failures never block the workflow. Link: x_calendar_event_id.
    # ──────────────────────────────────────────────────────────────────
    def _event_calendar_window(self):
        """Return (start_dt, stop_dt) as naive-UTC datetimes.

        The event's start / end times on the form are LOCAL wall-clock
        times (e.g. 9:00 PM in Idaho). calendar.event stores start/stop
        as naive UTC. So we compose the local wall-clock datetime,
        localize it to the user's tz, convert to UTC, and strip tzinfo.
        Without this dance, "9:00 PM" was being written as
        2026-08-08 21:00 naive-UTC, which then renders as 3:00 PM MDT /
        2:00 PM MST — the wrong time by exactly the user's UTC offset.
        """
        self.ensure_one()
        from datetime import datetime, time as dtime, timedelta
        d = self.x_event_date
        if not d and self.date_deadline:
            dl = self.date_deadline
            d = (fields.Datetime.context_timestamp(self, dl).date()
                 if hasattr(dl, 'hour') else dl)
        if not d:
            return None, None

        def _to_time(val, default_h):
            if not val:
                return dtime(default_h, 0)
            h = int(val)
            m = int(round((val - h) * 60))
            return dtime(min(h, 23), min(m, 59))

        # Compose local wall-clock datetimes
        local_start = datetime.combine(d, _to_time(self.x_event_start_time, 9))
        if self.x_event_end_time and self.x_event_end_time > (
                self.x_event_start_time or 0):
            local_stop = datetime.combine(d, _to_time(self.x_event_end_time, 17))
        else:
            local_stop = local_start + timedelta(hours=2)

        # Convert local -> UTC via user's tz. Fall back to UTC if the
        # user has no tz set (in which case there's nothing to shift).
        tz_name = (
            self.env.user.tz
            or self.env.context.get('tz')
            or 'UTC'
        )
        try:
            local_tz = pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            local_tz = pytz.UTC
        utc = pytz.UTC

        start_utc = local_tz.localize(local_start).astimezone(utc).replace(
            tzinfo=None)
        stop_utc = local_tz.localize(local_stop).astimezone(utc).replace(
            tzinfo=None)
        return start_utc, stop_utc

    def _ensure_placeholder_calendar(self):
        """Create a greyed (tentative) calendar entry if none exists yet."""
        self.ensure_one()
        if self.x_calendar_event_id or not self.x_is_event:
            return
        start, stop = self._event_calendar_window()
        if not start:
            return
        try:
            ev = self.env['calendar.event'].sudo().create({
                'name': _("TENTATIVE: %s", self.name or 'Event'),
                'start': start,
                'stop': stop,
                'show_as': 'free',
                'description': self.description or '',
                'x_event_task_id': self.id,
            })
            self.x_calendar_event_id = ev.id
        except Exception as e:  # noqa: BLE001 - never block the workflow
            _logger.warning("Event calendar placeholder failed: %s", e)

    def _promote_calendar(self):
        """Promote the placeholder to a confirmed booking on approval.

        On approval we also reassign the calendar.event's organizer
        (``user_id``) to the settings 'Events Calendar User'. The Elks
        Calendar publisher gathers calendar.event where
        ``user_id == publication.calendar_id``, so this is what makes an
        approved event populate the printed calendar automatically. The
        tentative placeholder stays off that user's calendar until now.
        """
        self.ensure_one()
        if not self.x_calendar_event_id:
            self._ensure_placeholder_calendar()
        if self.x_calendar_event_id:
            start, stop = self._event_calendar_window()
            vals = {'name': self.name or 'Event', 'show_as': 'busy',
                    'description': self.description or ''}
            if start:
                vals.update({'start': start, 'stop': stop})
            settings = self._event_settings()
            cal_user = settings.x_event_calendar_user_id if settings else False
            if cal_user:
                vals['user_id'] = cal_user.id
                vals['partner_ids'] = [(4, cal_user.partner_id.id)]
            self.x_calendar_event_id.sudo().write(vals)

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Marketing listing (Events app)
    # HUMAN: When a booking is flagged as an Elks Event (the lodge's own
    #        event, not a billed rental), auto-create a public listing in
    #        the Events app so it can be marketed. Unchecking archives it.
    # AI: _sync_marketing_event() creates/updates the linked event.event.
    #     Best-effort; never blocks the workflow. Link: x_marketing_event_id.
    # ──────────────────────────────────────────────────────────────────
    _MARKETING_SYNC_KEYS = frozenset((
        'name', 'x_is_elks_event', 'x_event_date',
        'x_event_start_time', 'x_event_end_time',
        'x_event_start_sel', 'x_event_end_sel',
        'x_event_start_time_text', 'x_event_end_time_text',
        'date_deadline', 'description', 'x_event_type',
    ))

    def _sync_marketing_event(self):
        """Create / update the Events-app listing for an Elks Event.

        Only fires for tasks flagged both as an event (x_is_event) and as
        the lodge's own Elks Event (x_is_elks_event). Unchecking the Elks
        Event flag archives the listing so it drops off the public site.
        """
        if 'event.event' not in self.env:
            return
        Event = self.env['event.event'].sudo()
        for rec in self:
            try:
                if not (rec.x_is_event and rec.x_is_elks_event):
                    # No longer an Elks Event -> retire any existing listing.
                    if rec.x_marketing_event_id:
                        rec.x_marketing_event_id.active = False
                    continue
                start, stop = rec._event_calendar_window()
                if not start:
                    continue
                tz_name = rec.env.user.tz or rec.env.context.get('tz') or 'UTC'
                vals = {
                    'name': rec.name or _('Elks Event'),
                    'date_begin': start,
                    'date_end': stop or start,
                    'date_tz': tz_name,
                }
                if 'description' in Event._fields:
                    vals['description'] = rec.description or ''
                ev = rec.x_marketing_event_id
                if ev:
                    if not ev.active:
                        ev.active = True
                    ev.write(vals)
                else:
                    ev = Event.create(vals)
                    rec.with_context(_marketing_sync=True).x_marketing_event_id = ev.id
            except Exception as e:  # noqa: BLE001 - never block the workflow
                _logger.warning(
                    "Marketing event sync failed for task %s: %s", rec.id, e)

    def action_open_marketing_event(self):
        """Smart-button: open the linked Events-app listing."""
        self.ensure_one()
        if not self.x_marketing_event_id:
            self._sync_marketing_event()
        if not self.x_marketing_event_id:
            return False
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'event.event',
            'res_id': self.x_marketing_event_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # ──────────────────────────────────────────────────────────────────
    # SECTION: After-action review
    # HUMAN: When an event moves to the After Action stage we (1) prompt the
    #        coordinator to jot notes and review the P&L, and (2) email the
    #        customer a feedback-survey link. When the customer finishes the
    #        survey the coordinator gets an activity + email (see
    #        survey_user_input.py). A P&L button rides the form in these
    #        stages, and the After-Action menu filters to these events.
    # AI: _on_enter_after_action() fires once (guarded by
    #     x_after_action_started); _send_customer_survey() creates a
    #     survey.user_input and mails its link.
    # ──────────────────────────────────────────────────────────────────
    def _on_enter_after_action(self):
        self.ensure_one()
        if self.x_after_action_started:
            return
        self.x_after_action_started = True
        self._create_after_action_activity()
        self._send_customer_survey()

    def _after_action_user(self):
        """Best pick for the coordinator user to notify / assign."""
        self.ensure_one()
        emp = self.x_coordinator_employee_id
        if emp and emp.user_id:
            return emp.user_id
        if 'user_ids' in self._fields and self.user_ids:
            return self.user_ids[0]
        return self.env.user

    def _create_after_action_activity(self):
        """Prompt the coordinator to add notes and review the P&L."""
        self.ensure_one()
        try:
            self.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=_("After-action: add notes & review Event P&L"),
                note=_("This event has moved to After Action. Add your "
                       "notes/thoughts on the After-Action tab and review the "
                       "Event P&L."),
                user_id=self._after_action_user().id)
        except Exception as e:  # noqa: BLE001 - never block the workflow
            _logger.warning(
                "After-action activity failed for task %s: %s", self.id, e)

    def _send_customer_survey(self):
        """Create a feedback-survey response for the customer and email the
        link. No-op if Surveys isn't installed or no survey is configured."""
        self.ensure_one()
        if 'survey.user_input' not in self.env:
            return
        settings = self._event_settings()
        survey = settings.x_customer_survey_id if settings else False
        # Skip if unset or archived — an inactive survey's public link 404s.
        if not survey or not survey.active:
            return
        email = self.x_customer_email or (
            self.partner_id.email if self.partner_id else False)
        if not email:
            return
        try:
            answer = self.x_customer_survey_input_id
            if not answer:
                answer = self.env['survey.user_input'].sudo().create({
                    'survey_id': survey.id,
                    'partner_id': self.partner_id.id if self.partner_id else False,
                    'email': email,
                    'x_event_id': self.id,
                })
                self.x_customer_survey_input_id = answer.id
            base = self.get_base_url()
            link = '%s/survey/start/%s?answer_token=%s' % (
                base, survey.access_token, answer.access_token)
            template = self.env.ref(
                'elksevent.mail_template_customer_survey',
                raise_if_not_found=False)
            if template:
                template.sudo().with_context(
                    survey_link=link).send_mail(self.id, force_send=True)
        except Exception as e:  # noqa: BLE001 - never block the workflow
            _logger.warning(
                "Customer survey send failed for task %s: %s", self.id, e)

    def action_open_event_pl(self):
        """Print the single-event P&L (used by the After-Action button)."""
        self.ensure_one()
        return self.env.ref(
            'elksevent.action_report_event_pl_single').report_action(self)

    def action_print_insurance_worksheet(self):
        """Print the K&K rental-insurance application worksheet, pre-filled
        from the event so staff can complete the online form for someone."""
        self.ensure_one()
        return self.env.ref(
            'elksevent.action_report_event_insurance').report_action(self)

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Board agenda (Secretary)
    # HUMAN: One consolidated list of every event awaiting board / floor
    #        review, so the Secretary can print it for the meeting or email
    #        it out ahead of time instead of hand-collating each request.
    #        Columns: Title, Type, Date, Elk (member?), Event Total.
    # AI: _board_agenda_events() searches the pending set; the print/email
    #     actions render report_event_board_agenda over that set.
    # ──────────────────────────────────────────────────────────────────
    @api.model
    def _board_agenda_events(self):
        """Events currently pending Board review, date-ordered."""
        return self.search([
            ('x_is_event', '=', True),
            ('parent_id', '=', False),
            ('x_approval_state', '=', 'board'),
        ], order='x_event_date, name')

    @api.model
    def _board_agenda_report_action(self):
        """Return the printable agenda report for all pending events."""
        events = self._board_agenda_events()
        if not events:
            raise UserError(_(
                "There are no events awaiting board or floor review right now."))
        report = self.env.ref('elksevent.action_report_event_board_agenda')
        return report.report_action(events)

    def action_email_board_agenda(self):
        """Render the agenda to PDF and open an email with it attached."""
        events = self._board_agenda_events()
        if not events:
            raise UserError(_(
                "There are no events awaiting board or floor review right now."))
        report = self.env.ref('elksevent.action_report_event_board_agenda')
        pdf_content, _ct = report._render_qweb_pdf(
            report.report_name, events.ids)
        today = fields.Date.context_today(self)
        fname = 'Board_Agenda_%s.pdf' % today.strftime('%Y%m%d')
        proj = events[:1].project_id
        attachment = self.env['ir.attachment'].create({
            'name': fname,
            'type': 'binary',
            'datas': base64.b64encode(pdf_content),
            'mimetype': 'application/pdf',
            'res_model': 'project.project' if proj else False,
            'res_id': proj.id if proj else False,
        })
        ctx = {
            'default_composition_mode': 'comment',
            'default_subject': 'Events for Board Review - %s' % (
                today.strftime('%m/%d/%Y')),
            'default_body': Markup(
                '<p>Attached is the consolidated list of events awaiting '
                'board review.</p>'),
            'default_attachment_ids': [(6, 0, attachment.ids)],
        }
        if proj:
            ctx['default_model'] = 'project.project'
            ctx['default_res_ids'] = proj.ids
        return {
            'type': 'ir.actions.act_window',
            'name': _("Email Board Agenda"),
            'res_model': 'mail.compose.message',
            'view_mode': 'form',
            'target': 'new',
            'context': ctx,
        }

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Customer emails
    # HUMAN: Sends the customer the right note at each step (received,
    #        approved, denied, deposit received) and a balance-due reminder
    #        a few weeks before the event.
    # AI: _send_event_mail(xmlid) is a best-effort send (no email = no-op).
    #     The reminder cron _cron_event_balance_due_reminders fires on events
    #     dated exactly settings.x_balance_due_days_before from today.
    # ──────────────────────────────────────────────────────────────────
    def _send_event_mail(self, xmlid):
        """Send a mail template to the event customer (best-effort)."""
        self.ensure_one()
        if not self.x_customer_email:
            return
        tmpl = self.env.ref(xmlid, raise_if_not_found=False)
        if tmpl:
            tmpl.send_mail(self.id, force_send=False)

    @api.model
    def _cron_event_balance_due_reminders(self):
        """Email the balance-due reminder N days before each approved event."""
        settings = self.env['elks.lodge.settings'].sudo().search([], limit=1)
        days = (settings.x_balance_due_days_before if settings else 21) or 21
        target = fields.Date.context_today(self) + timedelta(days=days)
        events = self.search([
            ('x_is_event', '=', True),
            ('x_is_elks_event', '=', False),
            ('x_approval_state', '=', 'approved'),
            ('x_event_date', '=', target),
        ])
        for e in events:
            e._send_event_mail('elksevent.tmpl_event_balance_reminder')

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
        self._send_event_mail('elksevent.tmpl_event_deposit_received')

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Invoice generation — two single-line invoices (deposit + final)
    # HUMAN: Bills the customer in two clean charges: a deposit (e.g. 50%) to
    #        hold the date, then the balance. Each is ONE line so the customer
    #        sees a simple total; the member discount and Terms link still show.
    #        "Refresh from Quote" updates a still-draft invoice if the quote
    #        changed; a posted invoice must be cancelled and recreated.
    # AI: Base = x_quote_total (fallback x_total_billed). Portion split by
    #     settings.x_deposit_pct; member discount applied as line discount;
    #     tax from settings.x_event_tax_id (else none). x_event_invoice_kind
    #     enforces one-per-kind. These amounts are what payment_clover charges.
    # ──────────────────────────────────────────────────────────────────
    def _event_invoice_base(self):
        """Return (base, discount_pct, deposit_pct) for invoice math.

        ``base`` is the LIVE customer total (x_total_billed) — already net of
        the room / member / COL discount — so the invoice reflects current
        numbers even if the quote document wasn't rebuilt. disc is 0 (the
        room discount is surfaced as its own line, not applied again).
        """
        self.ensure_one()
        settings = self._event_settings()
        base = self.x_total_billed or self.x_quote_total or 0.0
        disc = 0.0
        deposit_pct = (settings.x_deposit_pct if settings else 50.0) or 0.0
        return base, disc, deposit_pct

    def _event_payment_distribution(self):
        """Itemize the event's charges and group them by income (GL) account so
        a payment (e.g. a Clover deposit) can be applied to the right accounts.

        Amounts are NET of any discount (coordinator fee is never discounted),
        so the itemized charges sum to x_total_billed and, with tax, to the
        grand total. Accounts come from the FRS product income accounts; a
        component with no mapped account shows as 'Unassigned'.
        """
        self.ensure_one()
        s = self._event_settings()
        # The discount applies to the ROOM only. Scale each room component by
        # rooms_net / room_income (0 for a COL waiver); event costs are billed
        # at FULL. The itemized charges still net to x_total_billed.
        rooms_net, _room_disc = self._event_room_discount()
        room_income = (sum(self.x_room_booking_ids.mapped('subtotal'))
                       or self.x_room_rental_rate or 0.0)
        room_factor = (rooms_net / room_income) if room_income else 0.0

        def acct(product):
            tmpl = product.product_tmpl_id if product else False
            a = tmpl.x_elks_income_account_id if tmpl else False
            return a.display_name if a else 'Unassigned'

        charges = []
        fac_prod = s.x_facility_product_id if s else False
        clean_prod = s.x_cleaning_product_id if s else False
        for rb in self.x_room_booking_ids:
            rprod = rb.room_id.x_product_id or fac_prod
            rate = (rb.rate or 0.0) * room_factor
            clean = (rb.cleaning_fee or 0.0) * room_factor
            svc = (rb.service_fee or 0.0) * room_factor
            if rate:
                charges.append({'name': 'Room Fee - %s' % rb.room_id.name,
                                'amount': rate, 'account': acct(rprod)})
            if clean:
                charges.append({'name': 'Cleaning Fee - %s' % rb.room_id.name,
                                'amount': clean, 'account': acct(clean_prod or rprod)})
            if svc:
                charges.append({'name': 'Service Fee - %s' % rb.room_id.name,
                                'amount': svc, 'account': acct(fac_prod or rprod)})

        cat_prod = {
            'bar': s.x_bar_product_id, 'catering': s.x_catering_product_id,
            'event': s.x_event_labor_product_id,
            'cleaning': s.x_cleaning_product_id, 'other': fac_prod,
        } if s else {}
        for cl in self.x_cost_line_ids:
            net = (cl.total or 0.0)  # event costs are not discounted
            if not net:
                continue
            prod = cat_prod.get(cl.cost_type_id.category) or fac_prod
            charges.append({'name': cl.name or (cl.cost_type_id.name or 'Charge'),
                            'amount': net, 'account': acct(prod)})

        if self.x_coordinator_fee:
            charges.append({'name': 'Event Coordinator Fee',
                            'amount': self.x_coordinator_fee,
                            'account': acct(s.x_coordinator_product_id if s else False)})

        tax = self.x_event_tax_amount or 0.0
        tax_acct = 'Sales Tax Payable'
        gl = {}
        for c in charges:
            gl[c['account']] = gl.get(c['account'], 0.0) + c['amount']
        if tax:
            gl[tax_acct] = gl.get(tax_acct, 0.0) + tax
        gl_list = sorted(({'account': k, 'amount': v} for k, v in gl.items()),
                         key=lambda x: x['account'])

        subtotal = sum(c['amount'] for c in charges)
        grand = subtotal + tax
        payments = []
        paid = 0.0
        if self.x_deposit_received:
            amt = self.x_deposit_amount or 0.0
            method_label = dict(
                self._fields['x_deposit_method'].selection).get(
                self.x_deposit_method) or 'Deposit'
            payments.append({
                'label': method_label,
                'amount': amt, 'date': self.x_deposit_date,
                'ref': self.x_deposit_reference or '',
            })
            paid += amt
        return {
            'charges': charges, 'gl': gl_list, 'subtotal': subtotal,
            'tax': tax, 'tax_acct': tax_acct, 'grand': grand,
            'payments': payments, 'paid': paid, 'balance': grand - paid,
        }

    def _event_terms_body_html(self):
        """Return the CURRENT body of the /event-terms website page so the
        Facility Usage Agreement prints the live Terms & Conditions (the page
        is editable in the Website builder). ASCII-normalized so the PDF engine
        does not mojibake curly punctuation. Returns Markup or False."""
        self.ensure_one()
        page = self.env['website.page'].sudo().search(
            [('url', '=', '/event-terms')], limit=1)
        view = page.view_id if page else False
        if not view:
            return False
        try:
            root = view.sudo()._get_combined_arch()
        except Exception:
            try:
                root = etree.fromstring(view.sudo().arch or '')
            except Exception:
                return False
        # The terms live inside the page body (#wrap); fall back to the root.
        wrap = root.find(".//*[@id='wrap']")
        node = wrap if wrap is not None else root
        inner = ''.join(
            etree.tostring(c, encoding='unicode') for c in node)
        if not inner.strip():
            return False
        repl = {
            '—': '-', '–': '-', '‒': '-', '―': '-',
            ' ': ' ', ' ': ' ', '·': '-',
            '‘': "'", '’': "'", '“': '"', '”': '"',
            '…': '...',
        }
        for k, v in repl.items():
            inner = inner.replace(k, v)
        # The website page stores its content double-escaped, and re-serializing
        # the arch escapes it again — so "&" arrives as "&amp;amp;" and prints
        # literally. Collapse ONE level of double-escaping for common entities
        # (never producing a bare "&", which keeps the HTML valid).
        inner = re.sub(
            r'&amp;(amp;|lt;|gt;|quot;|apos;|nbsp;|#\d+;|#x[0-9a-fA-F]+;)',
            r'&\1', inner)
        return Markup(inner)

    def _event_invoice_terms(self):
        """Link the invoice to the Rental Terms & Conditions page.

        Uses the Lodge Settings URL if one is set, otherwise the built-in
        editable /event-terms website page (absolute URL so it works in the
        PDF and the portal).
        """
        settings = self._event_settings()
        if settings and settings.x_event_terms_url:
            label = settings.x_event_terms_label or _("Rental Terms & Conditions")
            url = settings.x_event_terms_url
        else:
            base = self.env['ir.config_parameter'].sudo().get_param(
                'web.base.url') or ''
            url = (base.rstrip('/') + '/event-terms') if base else '/event-terms'
            label = _("Rental Terms & Conditions")
        note = '<a href="%s">%s</a>' % (url, label)
        if self.x_contract_notes:
            note += "<br/>" + self.x_contract_notes
        return note

    def _event_invoice_narration(self):
        """Assemble the invoice Terms block from the Lodge Settings billing
        options: an acceptance-by-payment clause, the deposit receipt, a media
        reminder, a link to the Terms page, and the full embedded Terms text.
        All payments (deposit or final) are acceptance of the Terms."""
        self.ensure_one()
        s = self._event_settings()
        parts = []
        if not s or s.x_invoice_terms_acceptance:
            parts.append(Markup(
                '<p><strong>Acceptance of Terms:</strong> Payment of any '
                'portion of this invoice, including the reservation fee / '
                'deposit, constitutes acceptance of the Rental Terms &amp; '
                'Conditions.</p>'))
        receipt = self._event_deposit_receipt_note()
        if receipt:
            parts.append(Markup('<p>%s</p>') % receipt)
        media = self._event_media_note()
        if media:
            parts.append(Markup('<p>%s</p>') % media)
        if not s or s.x_invoice_terms_link:
            parts.append(Markup('<p>%s</p>') % Markup(self._event_invoice_terms()))
        if not s or s.x_invoice_terms_embed:
            body = self._event_terms_body_html()
            if body:
                parts.append(Markup(
                    '<hr/><h4>Rental Terms &amp; Conditions</h4>'))
                parts.append(body)
        if s and s.x_invoice_signature_line:
            parts.append(Markup(
                '<hr/><table style="width:100%; margin-top:16px;">'
                '<tr><td style="width:60%;">Host signature: '
                '______________________________</td>'
                '<td>Date: __________</td></tr>'
                '<tr><td style="padding-top:14px;">Elks representative: '
                '____________________</td>'
                '<td style="padding-top:14px;">Date: __________</td></tr>'
                '</table>'))
        return Markup('').join(parts)

    def _attach_event_agreement(self, move):
        """Attach the signable Facility Usage Agreement PDF to an invoice."""
        self.ensure_one()
        try:
            pdf, _dummy = self.env['ir.actions.report']._render_qweb_pdf(
                'elksevent.report_event_contract', self.ids)
            self.env['ir.attachment'].create({
                'name': 'Facility Usage Agreement - %s.pdf' % (
                    self.name or 'Event'),
                'type': 'binary',
                'datas': base64.b64encode(pdf),
                'res_model': 'account.move',
                'res_id': move.id,
                'mimetype': 'application/pdf',
            })
        except Exception as e:  # noqa: BLE001 - never block invoicing
            _logger.warning(
                "Agreement attach failed for invoice of task %s: %s",
                self.id, e)

    def _event_media_note(self):
        """When signage/projector media is involved, the invoice reminds the
        host to deliver the media to the office 4 days prior."""
        self.ensure_one()
        has_signage = any(
            c.cost_type_id.code == 'marketing_signage'
            for c in self.x_cost_line_ids)
        if has_signage or self.x_need_projector:
            return _(
                "Any media for signage/projector display must be provided to "
                "the Lodge office at least 4 days prior to the event to ensure "
                "proper loading into the system.")
        return ''

    def _event_deposit_receipt_note(self):
        """A 'DEPOSIT PAID …' line for the invoice when the deposit is in —
        so both invoices double as a receipt for the customer."""
        self.ensure_one()
        if not self.x_deposit_received:
            return ''
        method = ''
        if self.x_deposit_method:
            method = dict(self._fields['x_deposit_method'].selection).get(
                self.x_deposit_method, '')
        parts = [_("DEPOSIT PAID — $%.2f", self.x_deposit_amount or 0.0)]
        if self.x_deposit_date:
            parts.append(_("received %s", self.x_deposit_date))
        if method:
            parts.append(_("via %s", method))
        if self.x_deposit_reference:
            parts.append(_("(ref: %s)", self.x_deposit_reference))
        return " ".join(parts)

    def _event_invoice_tax_cmd(self):
        settings = self._event_settings()
        if settings and settings.x_event_tax_id:
            return [(6, 0, settings.x_event_tax_id.ids)]
        return [(5, 0, 0)]

    def _event_invoice_line_name(self, label):
        parts = [label, self.name]
        if self.x_event_date:
            parts.append(str(self.x_event_date))
        if self.x_is_member and self.x_member_number:
            parts.append(_("Member #%s", self.x_member_number))
        return " — ".join(p for p in parts if p)

    def _event_invoice_portion(self, kind):
        """Gross amount and product/label for a deposit or final invoice."""
        base, disc, deposit_pct = self._event_invoice_base()
        settings = self._event_settings()
        if kind == 'deposit':
            gross = base * deposit_pct / 100.0
            product = settings.x_deposit_product_id if settings else False
            label = _("Reservation Fee")
        else:
            gross = base * (1.0 - deposit_pct / 100.0)
            product = settings.x_facility_product_id if settings else False
            label = _("Facility Usage and Rental")
        return gross, disc, product, label

    def _event_invoice_lines(self, kind):
        """Build the invoice_line_ids commands for a deposit / final invoice.

        DEPOSIT = the reservation-fee portion (deposit %, half by default).
        FINAL   = the FULL event total, less any deposit already PAID = the
                  remaining balance owed at that moment.
        Both itemize the room at retail + the discount line so the customer
        sees it, split taxable / non-taxable exactly like the quote so the tax
        matches. A paid deposit is flagged on the deposit invoice and credited
        on the final.
        """
        self.ensure_one()
        settings = self._event_settings()
        tax_cmd = self._event_invoice_tax_cmd()
        facility = settings.x_facility_product_id if settings else False
        dep_product = settings.x_deposit_product_id if settings else False

        def _line(label, amount, product, taxable=True):
            vals = {
                'name': self._event_invoice_line_name(label),
                'quantity': 1,
                'price_unit': amount,
                'discount': 0.0,
                'tax_ids': tax_cmd if taxable else [(5, 0, 0)],
            }
            if product:
                vals['product_id'] = product.id
            acc = self._event_income_account(product)
            if acc:
                vals['account_id'] = acc.id
            return (0, 0, vals)

        _base, _d, deposit_pct = self._event_invoice_base()
        rooms_net, room_disc = self._event_room_discount()
        room_retail = self.x_room_retail or 0.0
        ec_taxable = sum(c.total for c in self.x_cost_line_ids if c.x_taxable)
        ec_nontax = (self.x_eventcosts_total or 0.0) - ec_taxable
        coord = self.x_coordinator_fee or 0.0
        taxable_net = rooms_net + ec_taxable          # taxed
        nontax_net = ec_nontax + coord                # exempt
        disc_label = self._event_discount_label()

        if kind == 'deposit':
            portion, line_prod = deposit_pct / 100.0, dep_product
        else:  # final = the whole total
            portion, line_prod = 1.0, facility

        show_disc = (room_disc > 0.005
                     and (not settings or settings.x_invoice_show_discount))

        lines = []
        # ---- TAXABLE side (goods): room at retail + the discount ----
        if show_disc:
            if room_retail:
                lines.append(_line(_("Facility / Room Rental (retail)"),
                                   room_retail * portion, line_prod, True))
            if ec_taxable:
                lines.append(_line(_("Taxable Event Costs"),
                                   ec_taxable * portion, line_prod, True))
            lines.append(_line(_("Less: %s", disc_label),
                               -room_disc * portion, line_prod, True))
        elif taxable_net:
            tlabel = (_("Reservation Fee") if kind == 'deposit'
                      else _("Facility Usage and Rental"))
            lines.append(_line(tlabel, taxable_net * portion, line_prod, True))
        # ---- NON-TAXABLE side (services + coordinator) ----
        if nontax_net:
            slabel = (_("Reservation Fee - services") if kind == 'deposit'
                      else _("Event Services (non-taxable)"))
            lines.append(_line(slabel, nontax_net * portion, line_prod, False))

        # Deposit handling.
        dep_paid = self.x_deposit_amount or 0.0
        date_txt = (_(" on %s", self.x_deposit_date)
                    if self.x_deposit_date else "")
        if kind == 'final' and self.x_deposit_received and dep_paid > 0:
            # FULL total less the deposit already paid = remaining balance.
            lines.append(_line(_("Less: Deposit PAID%s", date_txt),
                               -dep_paid, dep_product, False))
        if kind == 'deposit' and self.x_deposit_received and dep_paid > 0:
            # Flag the reservation fee as paid on the deposit invoice.
            lines.append((0, 0, {
                'display_type': 'line_note',
                'name': _("*** DEPOSIT PAID%(when)s - $%(amt).2f ***",
                          when=date_txt, amt=dep_paid),
            }))
        return lines

    def _build_event_invoice(self, kind):
        self.ensure_one()
        if self.x_is_elks_event:
            raise UserError(_(
                "This is an Elks Event — it is not billed."))
        # Always refresh the quote first so the invoice bills from CURRENT
        # numbers (room waiver, event costs, coordinator, discount) — no need
        # to remember to click Build Quote separately.
        if self.x_sale_order_id:
            try:
                self._sync_quote_lines()
            except Exception as e:  # noqa: BLE001 - don't block invoicing
                _logger.warning(
                    "Quote refresh before invoice failed for %s: %s",
                    self.id, e)
        base = self.x_total_billed or self.x_quote_total or 0.0
        if base <= 0:
            raise UserError(_(
                "Nothing to invoice. Build the event quote first."))
        existing = self.x_invoice_ids.filtered(
            lambda m: m.state != 'cancel' and m.x_event_invoice_kind == kind)
        if existing:
            raise UserError(_(
                "A %(kind)s invoice already exists. Refresh it while draft, "
                "or cancel it before creating a new one.", kind=kind))

        settings = self._event_settings()
        partner = self._get_event_partner()
        move = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': partner.id,
            'x_event_id': self.id,
            'x_event_invoice_kind': kind,
            'invoice_origin': (
                self.x_sale_order_id.name if self.x_sale_order_id
                else self.name),
            'narration': self._event_invoice_narration(),
            'invoice_line_ids': self._event_invoice_lines(kind),
        })
        # Attach the signable Facility Usage Agreement (T&C + signatures).
        if not settings or settings.x_invoice_attach_agreement:
            self._attach_event_agreement(move)
        self.message_post(
            body=_("<b>%(label)s invoice created</b>: %(link)s",
                   label=(_("Reservation Fee") if kind == 'deposit'
                          else _("Final")),
                   link=move._get_html_link()),
            subtype_xmlid='mail.mt_note',
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _("Event Invoice"),
            'res_model': 'account.move',
            'res_id': move.id,
            'view_mode': 'form',
        }

    def _event_income_account(self, product):
        """Resolve an income account for an invoice line (with fallback)."""
        self.ensure_one()
        acc = False
        if product:
            tmpl = product.product_tmpl_id
            acc = (tmpl.property_account_income_id
                   or tmpl.categ_id.property_account_income_categ_id)
        if not acc:
            # Odoo 19 dropped account.account.deprecated; guard for it.
            domain = [('account_type', '=', 'income')]
            if 'deprecated' in self.env['account.account']._fields:
                domain.append(('deprecated', '=', False))
            acc = self.env['account.account'].search(domain, limit=1)
        return acc

    def action_create_deposit_invoice(self):
        return self._build_event_invoice('deposit')

    def action_create_final_invoice(self):
        return self._build_event_invoice('final')

    def action_refresh_invoice_from_quote(self):
        """Rebuild the lines on every DRAFT event invoice to current amounts.

        Rebuilds the quote first, then fully re-lays each draft deposit/final
        invoice (deposit % or full-minus-deposit) so the amounts owed reflect
        the latest room, event costs, coordinator, discount, and deposit paid.
        """
        self.ensure_one()
        if self.x_sale_order_id:
            try:
                self._sync_quote_lines()
            except Exception as e:  # noqa: BLE001
                _logger.warning(
                    "Quote refresh failed for %s: %s", self.id, e)
        drafts = self.x_invoice_ids.filtered(lambda m: m.state == 'draft')
        if not drafts:
            raise UserError(_(
                "No draft invoice to refresh. Posted invoices must be "
                "cancelled and recreated."))
        for mv in drafts:
            kind = mv.x_event_invoice_kind or 'final'
            mv.write({
                'invoice_line_ids': [(5, 0, 0)] + self._event_invoice_lines(kind),
                'narration': self._event_invoice_narration(),
            })
        self.message_post(
            body=_("Refreshed %s draft invoice(s) from the quote.", len(drafts)),
            subtype_xmlid='mail.mt_note',
        )
        return True

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
            'x_is_event_project': True,
        })
        # Link stages to new project
        for stage_id in stage_ids:
            stage = self.env['project.task.type'].browse(stage_id)
            stage.write({'project_ids': [(4, project.id)]})

        _logger.info("Created Elk Year event project: %s", project_name)
