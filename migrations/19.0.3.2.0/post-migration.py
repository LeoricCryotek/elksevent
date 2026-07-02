# -*- coding: utf-8 -*-
"""Force-recompute x_is_event so the 'Event Date' header label appears.

`x_is_event` is a STORED computed field. On databases where the Events
project flag (`x_is_event_project`) was set after tasks already existed,
the stored value can be a stale False -- which makes the task form keep
showing "Deadline" instead of "Event Date" in the header (that swap is
gated on x_is_event). Recomputing every task's flag on upgrade repairs
the stored value so the relabel takes effect immediately.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    tasks = env['project.task'].search([])
    if not tasks:
        return
    tasks._compute_is_event()
    tasks.flush_recordset(['x_is_event'])
