# -*- coding: utf-8 -*-
"""Add event link to account.move (customer invoices)."""
from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    x_event_id = fields.Many2one(
        'project.task', string="Event",
        domain="[('x_is_event', '=', True)]",
        help="Link this invoice to a specific event booking.",
    )
