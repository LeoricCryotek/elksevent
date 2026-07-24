# -*- coding: utf-8 -*-
"""Set P&L category + protected on built-in cost types; add the new ones.

The seed file (data/event_cost_types.xml) is noupdate=1, so on an EXISTING
database a reload won't set the new category/protected flags or create the
new named types. Do it here by code so the P&L category totals have their
built-in types.
"""
from odoo import SUPERUSER_ID, api

# code -> (name, category, taxable, is_labor)
BUILTINS = [
    ('catering_food', 'Catering Food', 'catering', True, False),
    ('catering_service', 'Catering Service', 'catering', False, False),
    ('bar_service', 'Bar Service', 'bar', False, False),
    ('bar_fee', 'Bar Fee', 'bar', True, False),
    ('setup_labor', 'Setup Hours', 'event', False, True),
    ('event_labor', 'Event Hours', 'event', False, True),
    ('after_hours', 'After Hours', 'event', False, True),
    ('event_workers', 'Event Workers', 'event', False, True),
    ('event_service', 'Event Service', 'event', False, False),
    ('event_supplies', 'Event Supplies', 'event', True, False),
    ('cleanup_labor', 'Cleanup Hours', 'cleaning', False, True),
    ('cleaning_service', 'Cleaning Service', 'cleaning', False, False),
    ('coordinator', 'Event Coordinator Fee', 'coordinator', False, False),
    ('other', 'Other', 'other', False, False),
]


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Type = env['elks.event.cost.type']
    seq = 10
    for code, name, category, taxable, is_labor in BUILTINS:
        rec = Type.search([('code', '=', code)], limit=1)
        vals = {
            'category': category,
            'protected': True,
            'is_labor': is_labor,
            'sequence': seq,
        }
        if rec:
            # Don't stomp a taxable flag the user may have tuned; only set
            # category/protected/labor and rename bar_service -> "Bar Service".
            if code == 'bar_service' and rec.name == 'Bar Services':
                vals['name'] = name
            rec.write(vals)
        else:
            vals.update({'name': name, 'code': code, 'taxable': taxable})
            Type.create(vals)
        seq += 1
