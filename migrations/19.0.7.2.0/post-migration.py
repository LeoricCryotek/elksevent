# -*- coding: utf-8 -*-
"""Enable the new Event Workers switch on events that already carry event-staff
hours.

The Event Service labor line is now gated by x_event_workers_use (mirroring the
Bar Use switch). Existing events set their event-staff hours via the planned
setup/event/teardown fields, so turn the switch on wherever those exist — their
Event Service line keeps building (est event hours falls back to the planned
sum when no worker count is entered).
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Task = env['project.task']
    events = Task.search([
        ('x_is_event', '=', True),
        ('x_event_workers_use', '=', False),
        '|', '|', '|',
        ('x_plan_setup_hours', '>', 0),
        ('x_plan_event_hours', '>', 0),
        ('x_plan_teardown_hours', '>', 0),
        ('x_est_event_hours', '>', 0),
    ])
    if events:
        events.write({'x_event_workers_use': True})
