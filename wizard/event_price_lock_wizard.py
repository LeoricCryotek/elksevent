# -*- coding: utf-8 -*-
"""'Lock Agreed Price' wizard for the Financials tab.

HUMAN
-----
Freezes the contract price at a SPECIFIC agreed total. Normally a price locks
automatically (customer approval or a deposit) at the live total. This wizard is
for older, already-booked events — especially ones where a deposit was paid and
later back-end changes pushed the total above what the customer actually agreed
to. When a deposit is on file, the wizard suggests "2 x the deposit" as the
agreed total (the common half-now/half-later arrangement), but the coordinator
can type any figure. Everything above the locked total then becomes Potential
Revenue Charges the Lodge absorbs — never billed to the customer.

AI
--
- elks.event.price.lock.wizard: event_id + agreed_grand (default 2x deposit when
  a deposit exists, else the live grand total).
- action_lock calls project.task._lock_contract_price('manual', agreed_grand),
  which scales the taxable / non-taxable snapshot to that total.
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ElksEventPriceLockWizard(models.TransientModel):
    _name = "elks.event.price.lock.wizard"
    _description = "Lock Agreed Price"

    event_id = fields.Many2one("project.task", string="Event", required=True)
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id)
    live_grand = fields.Monetary(
        "Current System Total", readonly=True, currency_field="currency_id",
        help="What the event totals right now (may be inflated by changes "
             "made after the customer agreed).")
    deposit_paid = fields.Monetary(
        "Deposit Paid", readonly=True, currency_field="currency_id")
    agreed_grand = fields.Monetary(
        "Agreed Total (incl. tax)", required=True,
        currency_field="currency_id",
        help="The grand total the customer actually agreed to. For a "
             "half-now/half-later deposit, this is twice the deposit. Billing "
             "and the remaining balance will use THIS figure.")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        event_id = res.get("event_id") or self.env.context.get(
            "default_event_id") or self.env.context.get("active_id")
        if event_id:
            event = self.env["project.task"].browse(event_id)
            res["event_id"] = event_id
            live = event.x_event_grand_total or 0.0
            paid = event.x_total_paid or 0.0
            res["live_grand"] = live
            res["deposit_paid"] = paid
            # Suggest 2x the deposit (the usual half-down arrangement) when a
            # deposit is on file; otherwise fall back to the live total.
            res["agreed_grand"] = round(paid * 2.0, 2) if paid > 0 else live
        return res

    def action_lock(self):
        self.ensure_one()
        event = self.event_id
        if event.x_price_locked:
            raise UserError(_(
                "This event's price is already locked. Unlock it first if you "
                "need to change the agreed total."))
        if (self.agreed_grand or 0.0) <= 0:
            raise UserError(_("Enter the agreed total (greater than zero)."))
        event._lock_contract_price("manual", grand_override=self.agreed_grand)
        return {"type": "ir.actions.act_window_close"}
