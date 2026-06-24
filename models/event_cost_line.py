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
- `cost_type` in COST_TYPE_CHOICES; labor types ('setup_labor',
  'event_labor','cleanup_labor') are what action_add_coordinator_fee checks.
- Summed by project.task._compute_financials into x_total_costs.
"""
from odoo import api, fields, models


# ──────────────────────────────────────────────────────────────────────
# SECTION: Cost categories
# HUMAN: The kinds of expense a line can represent.
# AI: Labor codes are referenced by name in project_task; keep stable.
# ──────────────────────────────────────────────────────────────────────


COST_TYPE_CHOICES = [
    ('setup_labor', 'Setup Hours'),
    ('event_labor', 'Event Hours'),
    ('cleanup_labor', 'Cleanup Hours'),
    ('event_workers', 'Event Workers'),
    ('supplies', 'Supplies'),
    ('linens', 'Linens'),
    ('bar_service', 'Bar Services'),
    ('services', 'Services'),
    ('coordinator', 'Event Coordinator Fee'),
    ('other', 'Other'),
]


class EventCostLine(models.Model):
    _name = "elks.event.cost.line"
    _description = "Event Cost Line"
    _order = "sequence, id"

    event_id = fields.Many2one(
        'project.task', string="Event", required=True, ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    cost_type = fields.Selection(
        COST_TYPE_CHOICES, string="Type", required=True, default='other',
    )
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
    purchase_order_id = fields.Many2one(
        'purchase.order', string="Linked PO",
        help="Optional link to an actual vendor purchase order.",
    )

    @api.depends('quantity', 'unit_cost')
    def _compute_total(self):
        for rec in self:
            rec.total = (rec.quantity or 0.0) * (rec.unit_cost or 0.0)
