# -*- coding: utf-8 -*-
"""Link calendar.event placeholders back to their event booking.

HUMAN
-----
When an event is submitted, the system drops a tentative (greyed) entry on
the lodge calendar so everyone can see the date is spoken for. Once the board
and floor approve, that entry is promoted to a confirmed booking. This field
just ties the calendar entry back to the event it came from.

AI
--
- Extends `calendar.event` with `x_event_task_id`.
- The lifecycle lives on project.task: `_ensure_placeholder_calendar()`
  (creates, show_as='free', name 'TENTATIVE: ...') and `_promote_calendar()`
  (show_as='busy', real name). project.task.x_calendar_event_id is the inverse.
- Created via sudo() from both staff actions and the public web controller.
"""
from odoo import fields, models


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Event link
    # HUMAN: Which event booking this calendar entry represents.
    # AI: Inverse of project.task.x_calendar_event_id; indexed; copy=False.
    # ──────────────────────────────────────────────────────────────────
    x_event_task_id = fields.Many2one(
        'project.task', string="Event Booking", copy=False, index=True,
        help="The event booking this calendar entry represents.",
    )
