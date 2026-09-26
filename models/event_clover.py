# -*- coding: utf-8 -*-
"""Top Clover point-of-sale items pulled for an event's window.

One row per item (by name) with quantity and dollars, populated by
project.task.action_pull_clover_sales() from the payment_clover module's
clover.sale / clover.sale.line records in the event's time window.
"""
from odoo import fields, models


class ElksEventCloverItem(models.Model):
    _name = "elks.event.clover.item"
    _description = "Event Clover Top Item"
    _order = "qty desc, amount desc, id"

    event_id = fields.Many2one(
        "project.task", string="Event", required=True, ondelete="cascade",
        index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char("Item")
    qty = fields.Float("Qty Sold")
    amount = fields.Monetary("Sales", currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id)
