# -*- coding: utf-8 -*-
"""Tag timeclock punches to an event so actual labor can be reconciled.

HUMAN
-----
When staff clock in on the timeclock, they can mark which event they're
working and whether it's event work or custodial/cleanup. That lets the
event compare the hours actually worked against the hours we charged for —
so the final bill can be trued-up to what really happened.

AI
--
- Extends `hr.attendance` with `x_event_id` (-> project.task) and
  `x_event_role` ('event' | 'custodial').
- project.task._compute_actual_hours sums `worked_hours` grouped by role for
  attendances pointing at the event. worked_hours is the lodge-policy value
  from elksattendance (raw clock span); we just read it.
"""
from odoo import api, fields, models
from .event_staff_assignment import ROLE_TO_ATTENDANCE


class HrAttendance(models.Model):
    _inherit = "hr.attendance"

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Event link
    # HUMAN: Which event this shift was worked for, what kind of work, and —
    #        optionally — the exact Event Cost line it was billed under so we
    #        can compare what we charged against the real labor cost.
    # AI: All optional; indexed event_id + cost line. Read by project.task
    #     actuals (_paid_labor_by_category) and the cost line true-up
    #     (elks.event.cost.line._compute_labor_trueup).
    # ──────────────────────────────────────────────────────────────────
    x_event_id = fields.Many2one(
        'project.task', string="Event", index=True,
        domain="[('x_is_event', '=', True)]",
        help="The event this shift was worked for.",
    )
    x_event_role = fields.Selection([
        ('event', 'Event Worker'),
        ('bartender', 'Bartender'),
        ('kitchen', 'Kitchen / Catering'),
        ('custodial', 'Cleaning / Custodial'),
    ], string="Event Role", default='event',
        help="Which kind of labor this shift counts as. Drives the actual "
             "labor cost rolled into the event's P&L on true-up.",
    )
    x_event_cost_line_id = fields.Many2one(
        'elks.event.cost.line', string="Charged As", index=True,
        domain="[('event_id', '=', x_event_id)]",
        help="Tie this shift to the Event Cost line it was billed under (e.g. "
             "Cleaning Service). The line then compares what we billed against "
             "the actual labor cost, flagging any under-billing.",
    )
    x_gratuity_share = fields.Monetary(
        "Gratuity", currency_field='currency_id',
        help="This shift's share of the event gratuity pool (for payroll). "
             "Set by the event's 'Distribute Gratuity' action.",
    )
    x_coordinator_fee_share = fields.Monetary(
        "Coordinator Fee", currency_field='currency_id',
        help="Flat coordinator-fee payout posted to this shift by the event's "
             "'Pay Coordinator' action (for payroll).",
    )
    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id)

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Auto-link a clock-in to the event it was planned for
    # HUMAN: If the coordinator assigned this person to an event (planned
    #        roster) and they clock in during that event's window, tag the
    #        punch to the event with the assigned role automatically.
    # AI: create() links only NEW, unlinked punches; matches on employee +
    #     the punch date falling in the event's setup..cleanup window (or the
    #     event date). Role maps via ROLE_TO_ATTENDANCE.
    # ──────────────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for att in records:
            if not att.x_event_id and att.employee_id and att.check_in:
                att._elks_autolink_event_assignment()
        return records

    def _elks_autolink_event_assignment(self):
        self.ensure_one()
        punch_date = self.check_in.date()
        assigns = self.env['elks.event.staff.assignment'].sudo().search(
            [('employee_id', '=', self.employee_id.id)])
        for a in assigns:
            evt = a.event_id
            start = (evt.x_requested_setup.date() if evt.x_requested_setup
                     else evt.x_event_date)
            end = (evt.x_cleanup_datetime.date() if evt.x_cleanup_datetime
                   else evt.x_event_date)
            d0 = start or evt.x_event_date
            d1 = end or evt.x_event_date
            in_window = d0 and d1 and d0 <= punch_date <= d1
            if in_window or evt.x_event_date == punch_date:
                self.x_event_id = evt.id
                self.x_event_role = ROLE_TO_ATTENDANCE.get(a.role, 'event')
                break
