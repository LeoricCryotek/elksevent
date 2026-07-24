# -*- coding: utf-8 -*-
"""Seed the new Cook / Kitchen-Support rates from the legacy flat kitchen rate.

The kitchen labor calc now splits into cooks and kitchen support, each with its
own rate (x_cook_rate / x_kitchen_support_rate). On existing databases those new
fields default to 0.0, which would zero-out catering labor until someone fills
them in. Seed both from the old x_kitchen_rate so pricing is unchanged out of the
box; staff can adjust them afterward.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Settings = env['elks.lodge.settings']
    for s in Settings.search([]):
        legacy = s.x_kitchen_rate or 0.0
        vals = {}
        if not s.x_cook_rate and legacy:
            vals['x_cook_rate'] = legacy
        if not s.x_kitchen_support_rate and legacy:
            vals['x_kitchen_support_rate'] = legacy
        if vals:
            s.write(vals)

    # Bar Use is a newer master switch; existing events that already carry bar
    # data would otherwise show an unchecked box (and hide their own bar fields
    # + skip the bar cost line). Turn it on wherever bar data exists.
    Task = env['project.task']
    events = Task.search([
        ('x_is_event', '=', True),
        ('x_bar_use', '=', False),
        '|', '|', '|',
        ('x_num_bars', '>', 0),
        ('x_bartender_count', '>', 0),
        ('x_need_beer_tub', '=', True),
        ('x_bar_gratuity', '>', 0),
    ])
    if events:
        events.write({'x_bar_use': True})
