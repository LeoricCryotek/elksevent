# -*- coding: utf-8 -*-
"""Move existing member events to the new MANUAL discount model.

The member discount used to apply automatically. It is now a manual, reason-
tagged discount that defaults to 50% for Member / Non-Profit. Backfill existing
member-flagged events with reason='member' and 50% so they carry the current
policy. Memorial (Celebration-of-Life) events are left alone here: their ROOM is
waived automatically, and any further discount is chosen manually.
"""
from odoo import SUPERUSER_ID, api

MEMBER_DEFAULT_PCT = 50.0


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Task = env['project.task']
    events = Task.search([
        ('x_is_event', '=', True),
        ('x_discount_reason', '=', False),
        ('x_is_member', '=', True),
    ])
    if events:
        events.write({
            'x_discount_reason': 'member',
            'x_discount_pct': MEMBER_DEFAULT_PCT,
        })
