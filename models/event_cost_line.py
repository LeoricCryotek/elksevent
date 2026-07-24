# -*- coding: utf-8 -*-
"""Event cost lines — internal expense tracking for an event.

HUMAN
-----
These are the lodge's *costs* for running an event (setup/event/cleanup
labor hours, supplies, linens, bar services, etc.), kept separate from what
the customer is charged. They feed the cost side of the P&L and the
"event-staff hours" reminder on the coordinator-fee button.

AI
--
- Model `elks.event.cost.line`; m2o `event_id` -> project.task; `total`
  is computed from quantity x unit_cost.
- `cost_type_id` -> elks.event.cost.type (configurable). Types flagged
  is_labor are what the coordinator-fee labor base checks (via the related
  `is_labor`), replacing the old hard-coded labor tuple.
- Summed by project.task._compute_financials into x_total_costs.
"""
from odoo import api, fields, models


# ──────────────────────────────────────────────────────────────────────
# SECTION: Cost categories
# HUMAN: The kinds of expense a line can represent.
# AI: Labor codes are referenced by name in project_task; keep stable.
# ──────────────────────────────────────────────────────────────────────


class EventCostLine(models.Model):
    _name = "elks.event.cost.line"
    _description = "Event Cost Line"
    _order = "sequence, id"

    event_id = fields.Many2one(
        'project.task', string="Event", required=True, ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    cost_type_id = fields.Many2one(
        'elks.event.cost.type', string="Type", required=True,
        default=lambda self: self.env['elks.event.cost.type'].search(
            [('code', '=', 'other')], limit=1),
        help="Category of cost. Manage the list under Elks Events -> "
             "Configuration -> Cost Types.",
    )
    is_labor = fields.Boolean(related='cost_type_id.is_labor', store=False)
    name = fields.Char("Description")
    quantity = fields.Float("Quantity", default=1.0)
    unit_cost = fields.Monetary(
        "Unit Cost", currency_field='currency_id',
    )
    total = fields.Monetary(
        "Total", currency_field='currency_id',
        compute='_compute_total', store=True,
    )
    currency_id = fields.Many2one(
        'res.currency', string="Currency",
        default=lambda self: self.env.company.currency_id,
    )
    counts_for_coordinator = fields.Boolean(
        "Incl. in Coordinator Fee",
        default=False,
        help="When checked, this line's total is included in the base "
             "amount used to calculate the Event Coordinator's fee.",
    )
    x_taxable = fields.Boolean(
        "Taxable", default=False,
        help="If checked, this charge is part of the taxable subtotal (goods) "
             "on the customer's 'Event Rental' line. Defaults from the Cost "
             "Type; services stay untaxed.",
    )

    @api.onchange('cost_type_id')
    def _onchange_cost_type_taxable(self):
        """Seed the taxable flag from the chosen type's default."""
        if self.cost_type_id:
            self.x_taxable = self.cost_type_id.taxable
    purchase_order_id = fields.Many2one(
        'purchase.order', string="Linked PO",
        help="Optional link to an actual vendor purchase order.",
    )

    @api.depends('quantity', 'unit_cost')
    def _compute_total(self):
        for rec in self:
            rec.total = (rec.quantity or 0.0) * (rec.unit_cost or 0.0)
