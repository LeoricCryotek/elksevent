"""Baseline newly added bar options without overwriting existing plan history."""
import json
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    callouts = env['elks.event.callout'].search([
        ('department', '=', 'bar'), ('sent_snapshot', '!=', False)])
    keys = ('x_bar_drink_tickets', 'x_bar_wrist_bands', 'x_bar_hosted')
    for callout in callouts:
        try:
            snapshot = json.loads(callout.sent_snapshot)
        except (TypeError, ValueError):
            continue
        if not isinstance(snapshot, dict):
            continue
        changed = False
        for key in keys:
            if key not in snapshot:
                snapshot[key] = callout._fmt_plan_value(callout.event_id, key)
                changed = True
        if changed:
            callout.sent_snapshot = json.dumps(snapshot)
