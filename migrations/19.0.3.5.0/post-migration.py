# -*- coding: utf-8 -*-
"""Link existing events' header date with the tab Event Date + start time.

The header "Event Date" is the task deadline; the tab keys off x_event_date +
x_event_start_time. Older/imported/web-created events had these unlinked (e.g.
a header of "Aug 8 at 5:00 PM" but a blank tab start time). Reconcile every
event once on upgrade so the two sides match and stay linked going forward.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    events = env['project.task'].search([('x_is_event', '=', True)])
    if events:
        events._reconcile_event_datetime()
