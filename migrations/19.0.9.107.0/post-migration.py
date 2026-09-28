# -*- coding: utf-8 -*-
"""Backfill a payment-log line for events whose deposit is already received,
so the deposit counts in Total Paid / Balance and on the invoices."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    events = env['project.task'].search([
        ('x_is_event', '=', True),
        ('x_deposit_received', '=', True),
        ('x_deposit_amount', '>', 0),
    ])
    if events:
        events.with_context(_deposit_sync=True)._sync_deposit_payment()
