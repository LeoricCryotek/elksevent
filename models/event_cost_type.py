# -*- coding: utf-8 -*-
"""Configurable list of Event Cost types.

HUMAN
-----
This is the "Type" dropdown on an event's Event Costs tab (Setup Hours, Bar
Services, Supplies, ...). It used to be a fixed list baked into the code; now
it's a normal list you manage under Elks Events -> Configuration -> Cost Types,
exactly like Rooms / Venues. Add, rename, reorder, or archive a type any time —
no developer needed.

AI
--
- Simple config model; `code` is a stable internal key ONLY for the seeded
  built-ins the logic cares about (the labor ones + coordinator). User-added
  types leave code blank.
- `is_labor` marks a type whose hours feed the coordinator-fee labor base
  (project.task._... checks cost_type_id.is_labor). Replaces the old hard-coded
  ('setup_labor','event_labor','cleanup_labor') tuple.
- Referenced by elks.event.cost.line.cost_type_id.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class EventCostType(models.Model):
    _name = "elks.event.cost.type"
    _description = "Event Cost Type"
    _order = "sequence, name"

    name = fields.Char("Type", required=True, translate=True)
    category = fields.Selection([
        ('catering', 'Catering'),
        ('bar', 'Bar'),
        ('event', 'Event Services'),
        ('cleaning', 'Cleaning'),
        ('coordinator', 'Coordinator'),
        ('other', 'Other'),
    ], string="P&L Category", default='other', required=True,
        help="Which P&L bucket this type tallies into (the event totals "
             "Event Costs lines by category).")
    protected = fields.Boolean(
        "Built-in", default=False,
        help="Built-in type used by the P&L totals — cannot be deleted.")

    def unlink(self):
        if any(t.protected for t in self):
            raise UserError(_(
                "Built-in cost types feed the P&L category totals and can't be "
                "deleted. You can archive one you don't use instead."))
        return super().unlink()
    code = fields.Char(
        "Code",
        help="Stable internal key for built-in types the app logic relies on "
             "(leave blank for your own types).",
    )
    is_labor = fields.Boolean(
        "Counts as Labor",
        help="Tick for staff-hour types (setup/event/cleanup). Their hours "
             "feed the base used to compute the Event Coordinator fee.",
    )
    taxable = fields.Boolean(
        "Taxable", default=False,
        help="Default taxable state for new lines of this type. Tick for "
             "GOODS (e.g. supplies, linens); leave off for SERVICES (labor, "
             "bar service, coordinator). Staff can still override per line.",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
