# -*- coding: utf-8 -*-
"""'Supplemental Invoice' wizard for the Financials tab.

HUMAN
-----
After an event is already billed/paid, the coordinator can OPTIONALLY bill the
customer for extra, agreed-upon charges (e.g. they used more space than booked,
or asked for an add-on afterward). This wizard lets the coordinator tick exactly
which charges to put on that supplemental invoice, requires a note (defaulting
to "As discussed for the additional service fees"), and the invoice is Due
Immediately. It never touches the locked deposit/final invoices or the agreed
contract price — it's a brand-new, separate bill.

AI
--
- elks.event.supplemental.wizard: event_id + required note + a one2many of
  candidate charge lines (seeded from the event's cost lines, all unticked) plus
  any custom rows the coordinator adds.
- action_create builds an out_invoice (x_event_invoice_kind='supplemental') from
  only the ticked lines, invoice_date_due = today (due immediately).
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError

SUPP_DEFAULT_NOTE = "As discussed for the additional service fees"


class ElksEventSupplementalWizard(models.TransientModel):
    _name = "elks.event.supplemental.wizard"
    _description = "Supplemental Invoice"

    event_id = fields.Many2one("project.task", string="Event", required=True)
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id)
    note = fields.Text(
        "Invoice Note", required=True, default=SUPP_DEFAULT_NOTE,
        help="Prints on the supplemental invoice. Required — explain what the "
             "additional charges are for. Defaults to the standard wording.")
    line_ids = fields.One2many(
        "elks.event.supplemental.wizard.line", "wizard_id",
        string="Charges to Bill")
    total_selected = fields.Monetary(
        "Selected Total", compute="_compute_total_selected",
        currency_field="currency_id")

    @api.depends("line_ids.include", "line_ids.amount")
    def _compute_total_selected(self):
        for wiz in self:
            wiz.total_selected = sum(
                ln.amount for ln in wiz.line_ids if ln.include)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        event_id = res.get("event_id") or self.env.context.get(
            "default_event_id") or self.env.context.get("active_id")
        if event_id:
            res["event_id"] = event_id
            event = self.env["project.task"].browse(event_id)
            lines = []
            for cl in event.x_cost_line_ids:
                if (cl.total or 0.0) <= 0.0:
                    continue
                lines.append((0, 0, {
                    "include": False,
                    "name": cl.name or (cl.cost_type_id.name or _("Charge")),
                    "amount": cl.total,
                    "taxable": bool(cl.x_taxable),
                }))
            if lines:
                res["line_ids"] = lines
        return res

    def action_create(self):
        self.ensure_one()
        event = self.event_id
        if event.x_is_elks_event:
            raise UserError(_("This is an Elks Event — it is not billed."))
        if not (self.note or "").strip():
            raise UserError(_(
                "A note is required on a supplemental invoice. Explain what "
                "the additional charges are for."))
        chosen = self.line_ids.filtered(
            lambda l: l.include and (l.amount or 0.0) != 0.0 and l.name)
        if not chosen:
            raise UserError(_(
                "Tick at least one charge (with an amount) to bill, or add a "
                "custom line."))
        settings = event._event_settings()
        facility = settings.x_facility_product_id if settings else False
        partner = event._get_event_partner()
        tax_cmd = event._event_invoice_tax_cmd()
        acc = event._event_income_account(facility)

        inv_lines = []
        for ln in chosen:
            vals = {
                "name": event._event_invoice_line_name(ln.name),
                "quantity": 1,
                "price_unit": ln.amount,
                "tax_ids": tax_cmd if ln.taxable else [(5, 0, 0)],
            }
            if facility:
                vals["product_id"] = facility.id
            if acc:
                vals["account_id"] = acc.id
            inv_lines.append((0, 0, vals))

        move = self.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": partner.id,
            "x_event_id": event.id,
            "x_event_invoice_kind": "supplemental",
            "invoice_origin": (event.x_sale_order_id.name
                               if event.x_sale_order_id else event.name),
            "invoice_date": fields.Date.context_today(self),
            # Due immediately (same day).
            "invoice_date_due": fields.Date.context_today(self),
            "invoice_payment_term_id": False,
            "narration": self.note,
            "invoice_line_ids": inv_lines,
        })
        event.message_post(
            body=_("<b>Supplemental invoice created</b> (due immediately): "
                   "%(link)s — %(n)d charge(s), %(cur)s%(amt).2f. %(note)s",
                   link=move._get_html_link(), n=len(chosen),
                   cur=self.currency_id.symbol or "$",
                   amt=sum(chosen.mapped("amount")),
                   note=self.note),
            subtype_xmlid="mail.mt_note")
        return {
            "type": "ir.actions.act_window",
            "name": _("Supplemental Invoice"),
            "res_model": "account.move",
            "res_id": move.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }


class ElksEventSupplementalWizardLine(models.TransientModel):
    _name = "elks.event.supplemental.wizard.line"
    _description = "Supplemental Invoice Charge"

    wizard_id = fields.Many2one(
        "elks.event.supplemental.wizard", required=True, ondelete="cascade")
    currency_id = fields.Many2one(related="wizard_id.currency_id")
    include = fields.Boolean("Bill It?", default=False)
    name = fields.Char("Charge", required=True)
    amount = fields.Monetary("Amount", currency_field="currency_id")
    taxable = fields.Boolean(
        "Taxable", default=False,
        help="Charge sales tax on this line (same event tax as the quote).")
