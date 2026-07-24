# -*- coding: utf-8 -*-
"""Enable the new Cleaning switch on events that already carry cleaning hours.

The Cleaning Service labor line is now gated by x_cleaning_use (mirroring the
Event Workers switch). Existing events set cleaning via the planned cleaning
hours field, so turn the switch on wherever those exist - the line keeps
building (est cleaning hours falls back to the planned hours when no cleaner
count is entered).
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Task = env['project.task']
    events = Task.search([
        ('x_is_event', '=', True),
        ('x_cleaning_use', '=', False),
        '|',
        ('x_plan_cleaning_hours', '>', 0),
        ('x_est_cleaning_hours', '>', 0),
    ])
    if events:
        events.write({'x_cleaning_use': True})
