# -*- coding: utf-8 -*-
"""Convert cost lines from the old cost_type Selection to cost_type_id.

The Event Costs "Type" was a hard-coded Selection (varchar column cost_type);
it's now a Many2one to the configurable elks.event.cost.type. The seed data
(data/event_cost_types.xml) creates one type per old key with a matching
`code`. Map every existing cost line's old string onto the new record, and
default anything unmapped to the 'other' type. Reads the legacy column with
raw SQL because the Python field no longer exists.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    # Old column may already be gone on a fresh install; bail if so.
    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'elks_event_cost_line' AND column_name = 'cost_type'
    """)
    if not cr.fetchone():
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    Type = env['elks.event.cost.type']
    # code -> type id
    by_code = {t.code: t.id for t in Type.search([]) if t.code}
    other_id = by_code.get('other')

    cr.execute("SELECT id, cost_type FROM elks_event_cost_line")
    for line_id, old in cr.fetchall():
        type_id = by_code.get(old) or other_id
        if type_id:
            cr.execute(
                "UPDATE elks_event_cost_line SET cost_type_id = %s WHERE id = %s",
                (type_id, line_id))
