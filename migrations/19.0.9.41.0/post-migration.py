# -*- coding: utf-8 -*-
"""Seed the Event Rental Insurance default ($187) on the existing Lodge
Settings record (the field default only applies to brand-new records)."""


def migrate(cr, version):
    cr.execute(
        """SELECT 1 FROM information_schema.columns
           WHERE table_name = 'elks_lodge_settings'
             AND column_name = 'x_event_insurance_fee'""")
    if not cr.fetchone():
        return
    cr.execute(
        """UPDATE elks_lodge_settings
           SET x_event_insurance_fee = 187.0
           WHERE COALESCE(x_event_insurance_fee, 0) = 0""")
