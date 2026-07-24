# -*- coding: utf-8 -*-
"""Create the Marketing Signage Use + Exclusive Use built-in cost types.

The seed data is noupdate=1, so existing databases (already past 4.7.0) won't
get these new records from a reload — create them here.
"""
from odoo import SUPERUSER_ID, api

NEW = [
    ('marketing_signage', 'Marketing Signage Use'),
    ('exclusive_use', 'Exclusive Use'),
]


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Type = env['elks.event.cost.type']
    seq = 36
    for code, name in NEW:
        if not Type.search([('code', '=', code)], limit=1):
            Type.create({
                'name': name, 'code': code,
                'category': 'event', 'protected': True, 'sequence': seq,
            })
        seq += 1
