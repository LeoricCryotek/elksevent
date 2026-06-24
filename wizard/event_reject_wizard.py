# -*- coding: utf-8 -*-
"""Wizard to capture a rejection reason for an event (Board or Floor).

HUMAN
-----
When an event is turned down — at the Board or on the Floor — this pop-up
asks the approver to type why. The reason is saved on the event and shown in
its history, so nobody is left guessing why a request didn't go through.

AI
--
- TransientModel `elks.event.reject.wizard`; opened from
  project.task.action_board_reject / action_floor_reject with
  default_event_id + default_reject_stage in context.
- `action_reject` prefixes the stage label and calls
  event.action_record_floor_rejected(notes=...).
"""
from odoo import _, fields, models
from odoo.exceptions import UserError


class EventRejectWizard(models.TransientModel):
    _name = "elks.event.reject.wizard"
    _description = "Reject Event"

    event_id = fields.Many2one(
        "project.task", string="Event", required=True, readonly=True,
    )
    reject_stage = fields.Selection(
        [('board', 'Board'), ('floor', 'Floor')],
        string="Rejected At", default='board', readonly=True,
    )
    reason = fields.Text("Rejection Reason", required=True)

    def action_reject(self):
        """Record the rejection on the event."""
        self.ensure_one()
        if not self.event_id:
            raise UserError(_("No event linked to this rejection."))
        stage_label = dict(self._fields['reject_stage'].selection).get(
            self.reject_stage, '')
        note = self.reason
        if stage_label:
            note = f"({stage_label}) {note}"
        self.event_id.action_record_floor_rejected(notes=note)
        return {'type': 'ir.actions.act_window_close'}
