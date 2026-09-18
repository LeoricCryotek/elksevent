# -*- coding: utf-8 -*-
"""x_food_by_us becomes a computed field (Lodge Kitchen == caterer).

Before the field is redefined and recomputed, stash the events that had the
old "Food by Us" boolean set so post-migration can point them at the Lodge
Kitchen caterer (the new driver of that flag).
"""


def migrate(cr, version):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'project_task' AND column_name = 'x_food_by_us'
    """)
    if not cr.fetchone():
        return
    cr.execute("DROP TABLE IF EXISTS x_elksevent_foodbyus_tmp")
    cr.execute("""
        CREATE TABLE x_elksevent_foodbyus_tmp AS
        SELECT id FROM project_task
        WHERE x_food_by_us = TRUE AND x_caterer_id IS NULL
    """)
