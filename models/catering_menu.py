# -*- coding: utf-8 -*-
"""Catering menu packages and the per-event options quoted from them.

HUMAN
-----
The kitchen keeps a catalogue of dinner packages — a name, an entree, the
sides, and a per-person price. On an event, the coordinator adds one or more
packages with a guest count; each line multiplies price x guests. Ticking
"Ordered" posts that package to Event Costs (Catering Food) so it lands on the
invoice and the P&L. The Catering Quote PDF lists the options for the customer.

AI
--
- elks.catering.menu: the catalogue (name, entree, sides, price_per_person).
- elks.event.catering.line: a package quoted on one event (menu + guests ->
  line_total). An "ordered" line drives a catering_food Event Cost line via
  project.task._sync_labor_cost_lines (which owns all x_auto_source lines);
  create/write/unlink on the line re-trigger that rebuild.
"""
from odoo import api, fields, models


class ElksCateringMenu(models.Model):
    _name = "elks.catering.menu"
    _description = "Catering Menu Package"
    _order = "sequence, price_per_person, name"

    name = fields.Char("Menu Name", required=True)
    sequence = fields.Integer(default=10)
    price_per_person = fields.Monetary(
        "Price / Person", currency_field="currency_id")
    entree = fields.Char("Entree")
    sides = fields.Text(
        "Sides", help="One side per line (potato, vegetable, salad, roll, "
                      "dessert...). These print under the entree on the quote.")
    active = fields.Boolean(default=True)
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id)

    def component_lines(self):
        """Entree first, then each non-empty side line — for the PDF/portal."""
        self.ensure_one()
        out = []
        if self.entree:
            out.append(self.entree.strip())
        out += [ln.strip() for ln in (self.sides or "").splitlines()
                if ln.strip()]
        return out


class ElksEventCateringLine(models.Model):
    _name = "elks.event.catering.line"
    _description = "Event Catering Menu Option"
    _order = "sequence, id"

    event_id = fields.Many2one(
        "project.task", string="Event", required=True, ondelete="cascade",
        index=True)
    sequence = fields.Integer(default=10)
    menu_id = fields.Many2one(
        "elks.catering.menu", string="Menu", required=True)
    guest_count = fields.Integer("Guests")
    price_per_person = fields.Monetary(
        "Price / Person", currency_field="currency_id")
    line_total = fields.Monetary(
        "Total", compute="_compute_line_total", store=True,
        currency_field="currency_id")
    selected = fields.Boolean(
        "Ordered",
        help="Tick the option the customer ordered — it posts to Event Costs "
             "(Catering Food) for the invoice and P&L.")
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id)

    # Field changes that should rebuild the catering Event Cost line.
    _COST_KEYS = {"menu_id", "guest_count", "price_per_person", "selected"}

    @api.onchange("menu_id")
    def _onchange_menu_id(self):
        for line in self:
            if line.menu_id:
                line.price_per_person = line.menu_id.price_per_person

    @api.depends("guest_count", "price_per_person")
    def _compute_line_total(self):
        for line in self:
            line.line_total = (line.guest_count or 0) * (
                line.price_per_person or 0.0)

    def _resync_event_costs(self, events):
        events = events.filtered(lambda e: e.exists() and e.x_is_event)
        if events:
            events.with_context(_labor_sync=True)._sync_labor_cost_lines()

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        self._resync_event_costs(lines.mapped("event_id"))
        return lines

    def write(self, vals):
        res = super().write(vals)
        if self._COST_KEYS & set(vals):
            self._resync_event_costs(self.mapped("event_id"))
        return res

    def unlink(self):
        events = self.mapped("event_id")
        res = super().unlink()
        self._resync_event_costs(events)
        return res


class ElksEventMenuItem(models.Model):
    """A menu item the KITCHEN manager enters on their call-out portal.

    The manager types the entree, sides and price-per-plate for each dish they
    can prepare. On the event's Approval tab the coordinator ticks which the
    customer approved and enters how many plates to prepare. Approved items post
    to Event Costs (Kitchen Menu = plates x price-per-plate).
    """
    _name = "elks.event.menu.item"
    _description = "Event Kitchen Menu Item"
    _order = "sequence, id"

    event_id = fields.Many2one(
        "project.task", string="Event", required=True, ondelete="cascade",
        index=True)
    sequence = fields.Integer(default=10)
    entree = fields.Char("Entree")
    sides = fields.Text("Sides")
    price_per_plate = fields.Monetary(
        "Price / Plate", currency_field="currency_id")
    approved = fields.Boolean(
        "Approved by Customer",
        help="Tick the item(s) the customer approved. Approved items post to "
             "Event Costs as Kitchen Menu.")
    plates = fields.Integer(
        "Plates to Prepare",
        help="How many plates of this item to prepare (drives the Kitchen "
             "Menu charge).")
    line_total = fields.Monetary(
        "Total", compute="_compute_line_total", store=True,
        currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id)

    _COST_KEYS = {"price_per_plate", "approved", "plates"}

    @api.depends("plates", "price_per_plate")
    def _compute_line_total(self):
        for item in self:
            item.line_total = (item.plates or 0) * (
                item.price_per_plate or 0.0)

    def _resync_event_costs(self, events):
        events = events.filtered(lambda e: e.exists() and e.x_is_event)
        if events:
            events.with_context(_labor_sync=True)._sync_labor_cost_lines()

    @api.model_create_multi
    def create(self, vals_list):
        items = super().create(vals_list)
        self._resync_event_costs(items.mapped("event_id"))
        return items

    def write(self, vals):
        res = super().write(vals)
        if self._COST_KEYS & set(vals):
            self._resync_event_costs(self.mapped("event_id"))
        return res

    def unlink(self):
        events = self.mapped("event_id")
        res = super().unlink()
        self._resync_event_costs(events)
        return res
