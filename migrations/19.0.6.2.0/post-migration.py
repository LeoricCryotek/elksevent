# -*- coding: utf-8 -*-
"""Create the built-in 'Gratuity' cost type for existing databases."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Type = env['elks.event.cost.type']
    if not Type.search([('code', '=', 'gratuity')], limit=1):
        Type.create({
            'name': 'Gratuity', 'code': 'gratuity',
            'category': 'other', 'protected': True, 'sequence': 38,
        })
