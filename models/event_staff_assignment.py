# -*- coding: utf-8 -*-
"""Planned staff roster for an event.

HUMAN
-----
Before the event, the coordinator assigns specific employees / volunteers to
the roles they'll work (bartender, cook, kitchen support, event worker,
cleaning). This is the PLAN - who is supposed to show up - separate from the
actual timeclock punches. When an assigned person clocks in on the event day,
their punch is auto-linked to this event with the matching role (see
hr_attendance). The roster prints on the day-of coordinator sheet.

AI
--
- Model `elks.event.staff.assignment`; m2o event_id -> project.task (cascade),
  employee_id -> hr.employee, role selection.
- ROLE_TO_ATTENDANCE maps a planned role to the hr.attendance x_event_role
  ('kitchen' covers both cook and support since the timeclock has one kitchen
  role). Used by the clock-in auto-link and the requested-vs-assigned tallies.
- The elksvolunteer module adds a `position_id` (elks.volunteer.role) picker
  that sets `role` from the position's event_role, so the coordinator can pick
  from the configurable position catalogue instead of the fixed list.
"""
from odoo import fields, models

# Planned role -> hr.attendance.x_event_role
ROLE_TO_ATTENDANCE = {
    'bartender': 'bartender',
    'cook': 'kitchen',
    'kitchen_support': 'kitchen',
    'event_worker': 'event',
    'cleaning': 'custodial',
}


class EventStaffAssignment(models.Model):
    _name = "elks.event.staff.assignment"
    _description = "Event Staff Assignment (planned roster)"
    _order = "role, id"

    event_id = fields.Many2one(
        'project.task', string="Event", required=True, ondelete='cascade',
        index=True,
    )
    employee_id = fields.Many2one(
        'hr.employee', string="Employee / Volunteer", required=True,
    )
    role = fields.Selection([
        ('bartender', 'Bartender'),
        ('cook', 'Cook'),
        ('kitchen_support', 'Kitchen Support'),
        ('event_worker', 'Event Worker'),
        ('cleaning', 'Cleaning / Custodial'),
    ], string="Role", required=True, default='event_worker')
    note = fields.Char("Note")

    _sql_constraints = [
        ('event_employee_role_uniq',
         'unique(event_id, employee_id, role)',
         "This person is already assigned to that role for this event."),
    ]
