# -*- coding: utf-8 -*-
"""Extend maintenance.location with rentable venue fields.

Adds a flag so certain lodge locations (Bar, Lodge Room, Dining Room, etc.)
can be marked as rentable event venues with associated rates and fees.
"""
from odoo import fields, models


class MaintenanceLocation(models.Model):
    _inherit = "maintenance.location"

    x_is_rentable = fields.Boolean(
        "Available for Events",
        default=False,
        help="When checked, this location appears as a selectable venue "
             "for event bookings.",
    )
    x_room_rate = fields.Monetary(
        "Room Rate",
        currency_field='x_currency_id',
        help="Default rental rate for this room/space.",
    )
    x_cleaning_fee = fields.Monetary(
        "Cleaning Fee",
        currency_field='x_currency_id',
        help="Standard cleaning fee charged for this space.",
    )
    x_service_fee = fields.Monetary(
        "Service Fee",
        currency_field='x_currency_id',
        help="Other service fees (AV equipment, table setup, etc.).",
    )
    x_capacity = fields.Integer(
        "Capacity",
        help="Maximum number of guests this space can accommodate.",
    )
    x_currency_id = fields.Many2one(
        'res.currency', string="Currency",
        default=lambda self: self.env.company.currency_id,
    )
