# -*- coding: utf-8 -*-
"""Flag projects that hold event bookings.

HUMAN
-----
The lodge keeps event bookings in Project. There's a permanent "Events"
project plus an auto-created folder per Elk Year (e.g. "Events 2026-2027").
This flag simply marks a project as one that holds events, so the system
knows a task in it is a booking — no matter what the project is named. You
can flag any project as an Events project, and pick which one new requests
default into (in Lodge Settings).

AI
--
- Extends `project.project` with `x_is_event_project` (Boolean).
- project.task._compute_is_event treats a task as an event if its project is
  flagged OR matches the legacy 'Events %-%' name (kept for back-compat).
- The base project (data: project_event_bookings) is flagged; the year cron
  (_cron_create_elk_year_project) flags the projects it creates.
- Settings.x_event_project_id is the default target for new web/manual events.
"""
from odoo import fields, models


class ProjectProject(models.Model):
    _inherit = "project.project"

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Event project flag
    # HUMAN: Tick this if the project holds event bookings.
    # AI: Drives robust event detection; OR-ed with legacy name match.
    # ──────────────────────────────────────────────────────────────────
    x_is_event_project = fields.Boolean(
        "Event Project",
        help="When set, tasks in this project are treated as event bookings.",
    )
