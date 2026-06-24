# -*- coding: utf-8 -*-
"""Extend maintenance.location with rentable venue fields.

HUMAN
-----
Lodge spaces (Bar, Lodge Room, Dining Room, Seaport, Riverview, VIP, etc.)
already live in the maintenance module as "locations". These fields let the
lodge mark which ones can be rented for events, set their default rates/fees
and capacity, and point each room at the product used to bill it.

AI
--
- Extends `maintenance.location`. Monetary fields use `x_currency_id`.
- `x_is_rentable` filters the public venue picker and the venue list/action.
- `x_product_id` -> product.product: the quote builder
  (project.task._sync_quote_lines) creates a room line from this product;
  rooms with no product are skipped.
"""
from odoo import fields, models


class MaintenanceLocation(models.Model):
    _inherit = "maintenance.location"

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Rentable venue fields
    # HUMAN: Make a location bookable and give it its default pricing + product.
    # AI: Additive fields only. x_product_id is the bridge to the sale.order
    #     quote; rate/cleaning/service seed elks.event.room.booking via onchange.
    # ──────────────────────────────────────────────────────────────────
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
    x_product_id = fields.Many2one(
        'product.product', string="Rental Product",
        help="Product used on the event quote/invoice for renting this room.",
    )
