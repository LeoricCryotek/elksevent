"""Recalculate event P&L using actual labor/coordinator costs, excluding POs."""
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    events = env['project.task'].with_context(active_test=False).search([
        ('x_is_event', '=', True)])
    events._compute_financials()
    events._compute_event_sales()
    events._compute_is_ubi()
    completed = env.ref('elksevent.stage_completed', raise_if_not_found=False)
    if completed:
        events.filtered(lambda e: e.stage_id == completed).write({
            'x_closeout_hours': True, 'x_closeout_clover': True,
            'x_closeout_payment': True, 'x_closeout_reports': True})
