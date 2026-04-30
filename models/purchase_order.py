# -*- coding: utf-8 -*-
"""Extend purchase.order with an event link and budget guard.

Every PO tied to an event must not exceed the event's total billed
amount unless a user with the budget override permission provides
a written note.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    x_event_id = fields.Many2one(
        'project.task', string="Event",
        domain="[('x_is_event', '=', True)]",
        tracking=True,
        help="Link this PO to a specific event booking.",
    )
    x_event_budget_override = fields.Boolean(
        "Budget Override", copy=False,
        help="Allow this PO to exceed the event's billed total.",
    )
    x_event_budget_override_note = fields.Text(
        "Override Reason", copy=False,
    )
    x_event_budget_override_by = fields.Many2one(
        'res.users', string="Override By", copy=False,
    )

    def action_event_budget_override(self):
        """Set the budget override flag (requires permission)."""
        self.ensure_one()
        if not self.env.user.has_group('elksevent.group_event_budget_override'):
            raise UserError(_(
                "You do not have permission to override the event budget. "
                "Contact a manager or the Event Coordinator."
            ))
        if not self.x_event_budget_override_note:
            raise UserError(_(
                "Please enter an Override Reason before approving "
                "a budget override."
            ))
        self.write({
            'x_event_budget_override': True,
            'x_event_budget_override_by': self.env.uid,
        })
        self.message_post(
            body=_(
                "<b>Event Budget Override Approved</b><br/>"
                "Reason: %(reason)s<br/>By: %(user)s",
                reason=self.x_event_budget_override_note,
                user=self.env.user.name,
            ),
            subtype_xmlid='mail.mt_note',
        )

    @api.constrains('x_event_id', 'amount_total')
    def _check_event_budget(self):
        """Warn if event-linked POs would exceed the event's billed total."""
        for po in self:
            if not po.x_event_id or po.x_event_budget_override:
                continue
            event = po.x_event_id
            # Sum all POs linked to this event (including this one)
            all_pos = self.search([
                ('x_event_id', '=', event.id),
                ('state', '!=', 'cancel'),
            ])
            total_po_amount = sum(all_pos.mapped('amount_total'))
            billed = event.x_total_billed or 0.0
            if billed > 0 and total_po_amount > billed:
                raise UserError(_(
                    "Total PO costs ($%(po_total).2f) would exceed the "
                    "event's billed amount ($%(billed).2f).\n\n"
                    "Enter an Override Reason and click 'Budget Override' "
                    "if this is intentional.",
                    po_total=total_po_amount,
                    billed=billed,
                ))
