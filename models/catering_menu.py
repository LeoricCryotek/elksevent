# -*- coding: utf-8 -*-
"""Catering menu packages and the per-event options quoted from them.

HUMAN
-----
The kitchen keeps a catalogue of dinner packages — a name, a per-person price,
and the plate's components (entree, sides, dessert...). On an event, the
coordinator adds one or more packages with a guest count; each line multiplies
price x guests. The Catering Quote PDF lists the options for the customer to
choose from, exactly like the printed sheet the lodge hands out.

AI
--
- elks.catering.menu: the catalogue (name, price_per_person, components text).
- elks.event.catering.line: a package quoted on one event (menu + guests ->
  line_total). guest_count defaults from the event via the o2m context; the
  price copies from the menu on select and stays editable.
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
    components = fields.Text(
        "Included Items",
        help="One item per line (entree, sides, dessert...). These print as "
             "the menu's bullet list on the catering quote.")
    active = fields.Boolean(default=True)
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id)

    def component_lines(self):
        """The components as a clean list of non-empty lines (for the PDF)."""
        self.ensure_one()
        return [ln.strip() for ln in (self.components or "").splitlines()
                if ln.strip()]

    def _display_price(self):
        self.ensure_one()
        return self.price_per_person


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
        "Chosen", help="Tick the option the customer selected.")
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id)

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
