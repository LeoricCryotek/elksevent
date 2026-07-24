# -*- coding: utf-8 -*-
"""Clean the em-dash out of existing "Event Rental - non-taxable services" quote
lines.

The label was generated with an em-dash (U+2014) which the PDF engine renders
as mojibake ("Event Rental a-euro..."). New quotes use a hyphen; this fixes the
already-stored line names on existing sale orders so the contract, invoice, and
portal all read cleanly without a quote rebuild.
"""
from odoo import SUPERUSER_ID, api

EM_DASH = '\u2014'


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    lines = env['sale.order.line'].search([('name', 'like', EM_DASH)])
    for line in lines:
        if line.name and EM_DASH in line.name:
            line.name = line.name.replace(EM_DASH, '-')
