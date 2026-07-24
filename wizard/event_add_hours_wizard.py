# -*- coding: utf-8 -*-
"""Wizard: attach a worker's recorded timeclock shifts to an event.

HUMAN
-----
Instead of typing hours, the coordinator picks an employee, sees the shifts
that person actually recorded on the timeclock, ticks the ones worked for this
event, and assigns a role (Event Worker / Bartender / Cleaning). Those hours
then count toward the event's actual labor for the P&L.

AI
--
- TransientModel opened from project.task.action_open_add_hours with
  default_event_id. `attendance_ids` is domain-filtered (onchange) to the
  chosen employee's shifts not yet tied to an event. Apply writes x_event_id +
  x_event_role (sudo, since the coordinator is authorized to reconcile their
  event's labor even without global hr.attendance write).
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class EventAddHoursWizard(models.TransientModel):
    _name = "elks.event.add.hours.wizard"
    _description = "Add Timeclock Hours to Event"

    event_id = fields.Many2one(
        'project.task', string="Event", required=True, readonly=True)
    employee_id = fields.Many2one(
        'hr.employee', string="Employee", required=True)
    role = fields.Selection([
        ('event', 'Event Worker'),
        ('bartender', 'Bartender'),
        ('custodial', 'Cleaning / Custodial'),
    ], string="Role", required=True, default='event')
    attendance_ids = fields.Many2many(
        'hr.attendance', string="Recorded Shifts",
        help="The employee's timeclock shifts — tick the ones for this event.")

    @api.onchange('employee_id')
    def _onchange_employee(self):
        self.attendance_ids = [(5, 0, 0)]
        if not self.employee_id:
            return {'domain': {'attendance_ids': [('id', '=', False)]}}
        return {'domain': {'attendance_ids': [
            ('employee_id', '=', self.employee_id.id),
            ('x_event_id', '=', False),
        ]}}

    def action_apply(self):
        self.ensure_one()
        if not self.attendance_ids:
            raise UserError(_("Tick at least one recorded shift to add."))
        self.attendance_ids.sudo().write({
            'x_event_id': self.event_id.id,
            'x_event_role': self.role,
        })
        self.event_id.message_post(
            body=_("Added %(n)s shift(s) as %(role)s from the timeclock.",
                   n=len(self.attendance_ids),
                   role=dict(self._fields['role'].selection).get(self.role)),
            subtype_xmlid='mail.mt_note',
        )
        return {'type': 'ir.actions.act_window_close'}
