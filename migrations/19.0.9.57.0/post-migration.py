# -*- coding: utf-8 -*-
"""Create the Antlers Service built-in cost type.

The seed data (data/event_cost_types.xml) is noupdate=1, so existing databases
won't get the new record from a reload — create it here.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Type = env['elks.event.cost.type']
    if not Type.search([('code', '=', 'antlers')], limit=1):
        Type.create({
            'name': 'Antlers Service', 'code': 'antlers',
            'category': 'event', 'protected': True, 'sequence': 38,
        })
    # Seed the default Antlers fee if it hasn't been set.
    settings = env['elks.lodge.settings'].sudo().search([], limit=1)
    if settings and not settings.x_antlers_fee:
        settings.x_antlers_fee = 200.0
