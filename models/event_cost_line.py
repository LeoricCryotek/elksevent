# -*- coding: utf-8 -*-
"""Event cost lines — the 'PO section' for tracking actual event expenses.

Each line represents a cost category (setup labour, supplies, bar services,
coordinator fee, etc.).  Lines flagged *counts_for_coordinator* are used
to compute the coordinator fee when the fee type is 'percentage'.
"""
from odoo import api, fields, models


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
