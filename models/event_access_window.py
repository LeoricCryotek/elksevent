# -*- coding: utf-8 -*-
"""Additional access / setup windows for an event.

HUMAN
-----
Beyond the single Requested Setup and Requested Entry times, some events need
several access windows - e.g. "Silent auction setup Friday 9 AM", "Decorator
access Saturday 8 AM", "Cleanup Sunday 1 PM". Each window has a purpose, a
start (and optional end) date/time, and the staff/volunteers assigned to be
there. They print on the day-of coordinator sheet.

AI
--
- Model `elks.event.access.window`; m2o event_id -> project.task (cascade),
  m2m employee_ids -> hr.employee for the assigned staff. Ordered by start.
"""
from odoo import fields, models


class EventAccessWindow(models.Model):
    _name = "elks.event.access.window"
    _description = "Event Access / Setup Window"
    _order = "start_datetime, id"

    event_id = fields.Many2one(
        'project.task', string="Event", required=True, ondelete='cascade',
        index=True,
    )
    name = fields.Char(
        "Purpose", required=True,
        help="What the access is for (setup, decorating, cleanup, etc.).")
    start_datetime = fields.Datetime("Start", required=True)
    end_datetime = fields.Datetime("End")
    employee_ids = fields.Many2many(
        'hr.employee', string="Assigned Staff",
        help="Staff / volunteers assigned to be here for this window.")
    note = fields.Char("Note")
