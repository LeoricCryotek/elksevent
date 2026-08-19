# -*- coding: utf-8 -*-
"""Retire the Floor-vote step: Board approval is now final.

Any event still sitting in the old 'floor' (Floor Vote) state was board-
approved but not yet finalized. Move it back to 'board' so it reappears in
the Board Approval Queue and gets properly booked when an officer clicks
"Board Approve" (which now finalizes the event).
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    floor_events = env['project.task'].search(
        [('x_approval_state', '=', 'floor')])
    if floor_events:
        floor_events.write({'x_approval_state': 'board'})
