# -*- coding: utf-8 -*-
"""Through model linking an event to one or more rentable lodge rooms.

HUMAN
-----
One row per room a customer is renting for their event. It starts from the
room's default rate, cleaning and service fees, but a staffer can override
any of them for this particular event. The three add up to the room subtotal.

AI
--
- Model `elks.event.room.booking`; m2o `event_id` -> project.task (cascade),
  `room_id` -> maintenance.location (rentable only).
- `subtotal` = rate + cleaning_fee + service_fee (stored compute).
- `_onchange_room_id` seeds the three fees from the location defaults.
- The quote builder maps each booking to a sale line via room_id.x_product_id
  and is also what the double-booking constraint scans.
"""
from odoo import api, fields, models


class EventRoomBooking(models.Model):
    _name = "elks.event.room.booking"
    _description = "Event Room Booking"
    _order = "sequence, id"

    event_id = fields.Many2one(
        'project.task', string="Event", required=True, ondelete='cascade',
    )
    room_id = fields.Many2one(
        'maintenance.location', string="Room / Venue",
        required=True,
        domain="[('x_is_rentable', '=', True), ('active', '=', True)]",
    )
    sequence = fields.Integer(default=10)

    # Per-booking rate overrides (default from room)
    rate = fields.Monetary(
        "Room Rate", currency_field='currency_id',
    )
    cleaning_fee = fields.Monetary(
        "Cleaning Fee", currency_field='currency_id',
    )
    service_fee = fields.Monetary(
        "Service Fee", currency_field='currency_id',
    )
    subtotal = fields.Monetary(
        "Room Subtotal", currency_field='currency_id',
        compute='_compute_subtotal', store=True,
    )
    currency_id = fields.Many2one(
        'res.currency', string="Currency",
        default=lambda self: self.env.company.currency_id,
    )

    @api.depends('rate', 'cleaning_fee', 'service_fee')
    def _compute_subtotal(self):
        for rec in self:
            rec.subtotal = (
                (rec.rate or 0.0)
                + (rec.cleaning_fee or 0.0)
                + (rec.service_fee or 0.0)
            )

    @api.onchange('room_id')
    def _onchange_room_id(self):
        """Pre-fill rates from the selected room's defaults."""
        if self.room_id:
            self.rate = self.room_id.x_room_rate
            self.cleaning_fee = self.room_id.x_cleaning_fee
            self.service_fee = self.room_id.x_service_fee
