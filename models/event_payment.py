# -*- coding: utf-8 -*-
"""Customer payments recorded against an event.

HUMAN
-----
When a customer drops off a check, pays cash, or runs a card, staff record it
here — amount, method, reference (check #/txn), and the date received. The
Financials tab totals the payments and shows the remaining balance, and the
final invoice credits what's been paid.

AI
--
- elks.event.payment: one payment line on an event.
- project.task._amount_paid() sums these (falling back to the legacy single
  deposit fields when no payments are logged) for the balance + invoice credit.
"""
from odoo import api, fields, models


class ElksEventPayment(models.Model):
    _name = "elks.event.payment"
    _description = "Event Customer Payment"
    _order = "date desc, id desc"

    event_id = fields.Many2one(
        "project.task", string="Event", required=True, ondelete="cascade",
        index=True)
    date = fields.Date(
        "Date Received", required=True, default=fields.Date.context_today)
    amount = fields.Monetary("Amount", currency_field="currency_id")
    method = fields.Selection([
        ("cash", "Cash"),
        ("check", "Check"),
        ("card", "Credit/Debit Card"),
        ("other", "Other"),
    ], string="Method", required=True, default="check")
    reference = fields.Char(
        "Reference", help="Check number, card/transaction reference, etc.")
    note = fields.Char("Note")
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id)

    def _resync(self, events):
        events = events.filtered(lambda e: e.exists())
        if events:
            # Recompute balances / invoice-facing paid totals.
            events.invalidate_recordset(
                ["x_total_paid", "x_balance_remaining", "x_balance_due"])

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        for r in recs:
            r.event_id.message_post(body=(
                "Payment recorded: %s%.2f (%s%s) on %s." % (
                    r.currency_id.symbol or "$", r.amount or 0.0,
                    dict(self._fields["method"].selection).get(r.method, ""),
                    (" #%s" % r.reference) if r.reference else "",
                    r.date)))
        return recs
