# -*- coding: utf-8 -*-
"""Clean the em-dash out of existing Event Cost line names.

The auto labor/gratuity lines were labeled with an em-dash (U+2014) which the
PDF engine renders as mojibake (e.g. "Catering a-euro Cooks"). New lines use a
hyphen; this fixes the already-stored names so the P&L, coordinator sheet, and
Event Costs tab all read cleanly without a rebuild.
"""
from odoo import SUPERUSER_ID, api

EM_DASH = '\u2014'


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    lines = env['elks.event.cost.line'].search([('name', 'like', EM_DASH)])
    for line in lines:
        if line.name and EM_DASH in line.name:
            line.name = line.name.replace(EM_DASH, '-')
