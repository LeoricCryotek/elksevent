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
from odoo import fields, models


class HrAttendance(models.Model):
    _inherit = "hr.attendance"

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Event link
    # HUMAN: Which event this shift was worked for, and what kind of work.
    # AI: Both optional; indexed event_id. Read by project.task actuals.
    # ──────────────────────────────────────────────────────────────────
    x_event_id = fields.Many2one(
        'project.task', string="Event", index=True,
        domain="[('x_is_event', '=', True)]",
        help="The event this shift was worked for.",
    )
    x_event_role = fields.Selection([
        ('event', 'Event Staff'),
        ('custodial', 'Custodial / Cleanup'),
    ], string="Event Work Type", default='event',
        help="Whether this shift counts as event-staff or custodial labor.",
    )
