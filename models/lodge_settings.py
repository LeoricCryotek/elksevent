# -*- coding: utf-8 -*-
"""Extend elks.lodge.settings with event coordinator fee configuration."""
from odoo import fields, models


class ElksLodgeSettings(models.Model):
    _inherit = "elks.lodge.settings"

    x_coordinator_fee_type = fields.Selection([
        ('fixed', 'Fixed Rate per Event'),
        ('percentage', 'Percentage of Room Income'),
    ], string="Coordinator Fee Type", default='percentage',
        help="How the event coordinator fee is calculated.",
    )
    x_coordinator_fixed_rate = fields.Monetary(
        "Coordinator Fixed Rate",
        currency_field='x_event_currency_id',
        help="Fixed dollar amount paid per event when fee type is 'Fixed Rate'.",
    )
    x_coordinator_percentage = fields.Float(
        "Coordinator Percentage", default=20.0,
        help="Percentage of room rental income paid as coordinator fee.",
    )
    x_event_currency_id = fields.Many2one(
        'res.currency', string="Currency",
        default=lambda self: self.env.company.currency_id,
    )
