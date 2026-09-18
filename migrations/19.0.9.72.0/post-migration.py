# -*- coding: utf-8 -*-
"""Backfill attendance/POS PINs for employees that don't have one.

PIN = last 4 digits of their phone (work, then mobile, then private), or 0000
when there's no phone on file. Only touches employees whose PIN is empty, so
existing PINs are never overwritten.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    emps = env['hr.employee'].with_context(active_test=False).search([
        '|', ('pin', '=', False), ('pin', '=', ''),
    ])
    for emp in emps:
        phone = emp.work_phone or getattr(emp, 'mobile_phone', '') \
            or getattr(emp, 'private_phone', '') or ''
        digits = ''.join(ch for ch in phone if ch.isdigit())
        emp.pin = digits[-4:] if len(digits) >= 4 else '0000'
