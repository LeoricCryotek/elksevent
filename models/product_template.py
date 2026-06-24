# -*- coding: utf-8 -*-
"""Tag products as event-rental items and attach an FRS income account.

HUMAN
-----
The event quote/invoice is built from ordinary Odoo products (Room, Bar,
Bartender, Linen, Catering, etc.). Two extra fields let the lodge mark a
product as an "event" item and tell it which line of the Elks chart of
accounts its income belongs to — so the AP report can show the money split
the way the treasurer books it.

AI
--
- Extends `product.template`. `x_is_event_product` is just a flag/filter.
- `x_elks_income_account_id` -> `elks.account` (domain account_type='income').
  Read by `sale.order.event_ap_breakdown()` via
  `product_id.product_tmpl_id.x_elks_income_account_id`.
- This is a NEW field, intentionally separate from Odoo's native
  `property_account_income_id` (which may be hidden by another module).
"""
from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Event rental product fields
    # HUMAN: Flag the product as an event item and pick its FRS income line.
    # AI: Both additive; no compute/constraint. Surfaced on the product form
    #     via view_product_template_form_event (Event Rental page).
    # ──────────────────────────────────────────────────────────────────
    x_is_event_product = fields.Boolean(
        "Event Product",
        help="Marks this product as an event-rental line item (room, bar, "
             "linen, catering, etc.).",
    )
    x_elks_income_account_id = fields.Many2one(
        'elks.account', string="FRS Income Account",
        domain="[('account_type', '=', 'income')]",
        help="FRS chart-of-accounts income line this product's revenue posts "
             "to. Used by the AP GL breakout report.",
    )
