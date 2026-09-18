# -*- coding: utf-8 -*-
"""Point previously "Food by Us" events at the Lodge Kitchen caterer.

Setting x_caterer_id = Lodge Kitchen re-drives the now-computed x_food_by_us
flag back to True for those events, preserving the in-house kitchen call-out.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    cr.execute("""
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'x_elksevent_foodbyus_tmp'
    """)
    if not cr.fetchone():
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    kitchen = env.ref(
        'elksevent.partner_lodge_kitchen', raise_if_not_found=False)
    if kitchen:
        cr.execute("SELECT id FROM x_elksevent_foodbyus_tmp")
        ids = [r[0] for r in cr.fetchall()]
        tasks = env['project.task'].browse(ids).exists().filtered(
            lambda t: not t.x_caterer_id)
        if tasks:
            tasks.write({'x_caterer_id': kitchen.id})
    cr.execute("DROP TABLE IF EXISTS x_elksevent_foodbyus_tmp")
