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
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError


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
        ("writeoff", "Write-Off / Waived"),
    ], string="Method", required=True, default="check")
    reference = fields.Char(
        "Reference", help="Check number, card/transaction reference, etc.")
    note = fields.Char("Note")
    is_deposit = fields.Boolean(
        "From Deposit", help="This payment line was created from the event's "
        "recorded deposit; it stays in sync with the Deposit fields.")
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id)

    # ------------------------------------------------------------------
    # Clover match — does the lodge's Clover point-of-sale show a payment
    # of this amount around the same date? If so, show a green check and
    # let staff drill into the Clover order. Computed live (not stored) so
    # it reflects the latest Clover sync. The clover.sale id is kept as a
    # plain Integer so elksevent does NOT hard-depend on payment_clover.
    # ------------------------------------------------------------------
    x_clover_sale_id = fields.Integer(
        "Clover Sale (id)", compute="_compute_clover_match")
    x_clover_matched = fields.Boolean(
        "Clover Match", compute="_compute_clover_match",
        help="A Clover point-of-sale payment of this amount was found within "
             "a few days of this payment date.")
    x_clover_info = fields.Char(
        "Clover Payment", compute="_compute_clover_match",
        help="The matching Clover order's date and total.")

    # How close the amounts must be, and how wide the date window is.
    _CLOVER_AMT_TOL = 0.01
    _CLOVER_DAYS = 3

    @api.depends("amount", "date")
    def _compute_clover_match(self):
        has_clover = "clover.sale" in self.env
        Sale = self.env["clover.sale"].sudo() if has_clover else None
        for r in self:
            r.x_clover_sale_id = 0
            r.x_clover_matched = False
            r.x_clover_info = ""
            if not has_clover or not r.amount or not r.date:
                continue
            start = fields.Datetime.to_datetime(r.date) - timedelta(
                days=self._CLOVER_DAYS)
            # +1 day so the whole last calendar day is covered.
            end = fields.Datetime.to_datetime(r.date) + timedelta(
                days=self._CLOVER_DAYS + 1)
            cands = Sale.search([("date", ">=", start), ("date", "<=", end)])
            partner = r.event_id.partner_id
            best = None
            for s in cands:
                if abs((s.total_amount or 0.0) - r.amount) > self._CLOVER_AMT_TOL:
                    continue
                if partner and s.partner_id and s.partner_id == partner:
                    best = s
                    break
                if best is None:
                    best = s
            if best:
                r.x_clover_sale_id = best.id
                r.x_clover_matched = True
                r.x_clover_info = "%s · $%.2f" % (
                    best.date.strftime("%m/%d/%Y") if best.date else "",
                    best.total_amount or 0.0)

    def action_view_clover_sale(self):
        self.ensure_one()
        if not self.x_clover_sale_id or "clover.sale" not in self.env:
            raise UserError(_("No matching Clover payment is linked."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Clover Payment"),
            "res_model": "clover.sale",
            "res_id": self.x_clover_sale_id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }

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
            # A real down payment signifies agreement to the price -> lock it.
            if (r.method != 'writeoff' and (r.amount or 0.0) > 0.0
                    and 'x_price_locked' in r.event_id._fields
                    and not r.event_id.x_price_locked):
                r.event_id._lock_contract_price('deposit')
        return recs
