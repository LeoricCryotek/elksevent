# -*- coding: utf-8 -*-
"""Backfill the as-sent plan snapshot for existing call-outs.

Call-outs sent/certified before the change-tracking feature have no baseline,
so the manager's "what changed" highlight never fired. Capture the current plan
as their baseline now, so any FUTURE coordinator edit shows as a change.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    callouts = env['elks.event.callout'].search([
        ('state', 'in', ('sent', 'certified')),
        ('sent_snapshot', '=', False),
    ])
    for co in callouts:
        try:
            co._capture_snapshot()
        except Exception:  # noqa: BLE001 - never block the upgrade
            pass
