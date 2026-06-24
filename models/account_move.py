# -*- coding: utf-8 -*-
"""Add event links to account.move (customer invoices).

HUMAN
-----
A facility rental is billed as two customer invoices: a deposit (50%) to
secure the date, then the balance before the event. Each invoice is a single
line ("Event Deposit" / "Facility Usage and Rental") so the customer sees one
clean charge — these two fields say which event the invoice belongs to and
which of the two charges it is (which also matches the two Clover products).

AI
--
- Extends `account.move`. `x_event_id` -> project.task (domain x_is_event).
- `x_event_invoice_kind` in ('deposit','final'); set by
  project.task._build_event_invoice and used by its duplicate guard and by
  action_refresh_invoice_from_quote.
- project.task.x_invoice_ids is the inverse one2many on x_event_id.
"""
from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Event invoice links
    # HUMAN: Tie the invoice to its event and mark it deposit vs final.
    # AI: Both copy=False. kind drives one-invoice-per-kind guard upstream.
    # ──────────────────────────────────────────────────────────────────
    x_event_id = fields.Many2one(
        'project.task', string="Event",
        domain="[('x_is_event', '=', True)]",
        help="Link this invoice to a specific event booking.",
    )
    x_event_invoice_kind = fields.Selection([
        ('deposit', 'Event Deposit'),
        ('final', 'Event Final'),
    ], string="Event Invoice Kind", copy=False,
        help="Which event charge this invoice represents (matches the Clover "
             "product).",
    )
