# -*- coding: utf-8 -*-
"""Catering menu: split the retired free-text 'components' into entree + sides.

The catalogue used to store a single multiline 'components' field. It now has a
separate 'entree' (first line) and 'sides' (the rest). Backfill any existing
menu package so seeded/lodge-created rows keep their items after the upgrade.
"""


def migrate(cr, version):
    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'elks_catering_menu' AND column_name = 'components'
    """)
    if not cr.fetchone():
        return
    cr.execute("""
        SELECT id, components FROM elks_catering_menu
        WHERE components IS NOT NULL
          AND (entree IS NULL OR entree = '')
    """)
    for menu_id, components in cr.fetchall():
        lines = [ln.strip() for ln in (components or "").splitlines()
                 if ln.strip()]
        if not lines:
            continue
        entree = lines[0]
        sides = "\n".join(lines[1:])
        cr.execute(
            "UPDATE elks_catering_menu SET entree = %s, sides = %s "
            "WHERE id = %s", (entree, sides, menu_id))
