# -*- coding: utf-8 -*-
"""Wizard to record the Floor Vote result on an event (project.task).

HUMAN
-----
After the Board passes an event up to the membership floor, the Secretary
opens this little pop-up to record the vote: approved or rejected, the motion
number, the for/against tally, the meeting date, and any notes. That detail
is written into the event's history so there's a clean audit trail.

AI
--
- TransientModel `elks.event.floor.vote.wizard`; opened from
  project.task.action_floor_approve with default_event_id in context.
- `action_record_vote` assembles a notes string then calls
  event.action_record_floor_approved / _floor_rejected.
- Mirrors elkspurchase.floor.vote.wizard by design.
"""
from odoo import _, fields, models
from odoo.exceptions import UserError


class EventFloorVoteWizard(models.TransientModel):
    """Record the outcome of a Floor Vote on an event request.

    Captures vote result, motion number, vote counts, meeting date, and
    notes.  On confirmation it advances the event's approval state and
    posts the result to the event's chatter for the audit trail.
    Mirrors ``elkspurchase.floor.vote.wizard``.
    """
    _name = "elks.event.floor.vote.wizard"
    _description = "Record Floor Vote on Event"

    event_id = fields.Many2one(
        "project.task", string="Event", required=True, readonly=True,
    )
    vote_result = fields.Selection(
        [('approved', 'Approved'), ('rejected', 'Rejected')],
        string="Vote Result", required=True, default='approved',
    )
    motion_number = fields.Char(
        "Motion Number",
        help="The motion number from the Lodge meeting minutes.",
    )
    vote_for = fields.Integer("Votes For")
    vote_against = fields.Integer("Votes Against")
    vote_date = fields.Date(
        "Meeting Date", default=fields.Date.context_today, required=True,
    )
    notes = fields.Text(
        "Notes", help="Any conditions, amendments, or additional context.",
    )

    def action_record_vote(self):
        """Record the floor vote and advance the event."""
        self.ensure_one()
        evt = self.event_id
        if not evt:
            raise UserError(_("No event linked to this vote."))

        parts = []
        if self.motion_number:
            parts.append(f"Motion: {self.motion_number}")
        if self.vote_for or self.vote_against:
            parts.append(
                f"Vote: {self.vote_for} for / {self.vote_against} against")
        parts.append(f"Meeting date: {self.vote_date}")
        if self.notes:
            parts.append(self.notes)
        notes_text = "\n".join(parts)

        if self.vote_result == 'approved':
            evt.action_record_floor_approved(notes=notes_text)
        else:
            evt.action_record_floor_rejected(notes=notes_text)

        return {'type': 'ir.actions.act_window_close'}
