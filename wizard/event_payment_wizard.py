# -*- coding: utf-8 -*-
"""Quick 'Record Payment' wizard for the Financials tab.

Captures amount, method, reference and date, then writes an
elks.event.payment line on the event.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo import _


class ElksEventPaymentWizard(models.TransientModel):
    _name = "elks.event.payment.wizard"
    _description = "Record Event Payment"

    event_id = fields.Many2one(
        "project.task", string="Event", required=True)
    balance_remaining = fields.Monetary(
        related="event_id.x_balance_remaining", readonly=True,
        currency_field="currency_id")
    amount = fields.Monetary("Amount", required=True,
                             currency_field="currency_id")
    method = fields.Selection([
        ("cash", "Cash"),
        ("check", "Check"),
        ("card", "Credit/Debit Card"),
        ("other", "Other"),
    ], string="Payment Type", required=True, default="check")
    reference = fields.Char(
        "Reference / Check #",
        help="Check number, card/transaction reference, etc.")
    date = fields.Date("Date Received", required=True,
                       default=fields.Date.context_today)
    note = fields.Char("Note")
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id)

    @api.onchange("method")
    def _onchange_method(self):
        # Prefill the amount with the outstanding balance for convenience.
        if not self.amount and self.event_id:
            self.amount = self.event_id.x_balance_remaining or 0.0

    def action_record(self):
        self.ensure_one()
        if self.amount <= 0:
            raise UserError(_("Enter a payment amount greater than zero."))
        if self.method == "check" and not self.reference:
            raise UserError(_("Enter the check number in Reference."))
        self.env["elks.event.payment"].create({
            "event_id": self.event_id.id,
            "date": self.date,
            "amount": self.amount,
            "method": self.method,
            "reference": self.reference,
            "note": self.note,
        })
        return {"type": "ir.actions.act_window_close"}
