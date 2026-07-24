# -*- coding: utf-8 -*-
"""Extend elks.lodge.settings with event rental configuration.

HUMAN
-----
This is the one place to tune how event rentals price and bill: the
coordinator fee, how many guests per bartender, per-plate cost/charge, the
deposit percentage and when the balance is due, the member discount, the tax
to show, the Terms link, and which products to drop on every quote/invoice.
Change a number here and every new event follows it.

AI
--
- Single-row config model (inherited from elksfrs). Monetary fields use
  `x_event_currency_id`.
- Read across the module via project.task._event_settings() ->
  env['elks.lodge.settings'].search([], limit=1). Not in @api.depends of the
  computes that read it, so changing settings does NOT retro-recompute stored
  fields on existing events — by design.
- Product m2o fields are the defaults the quote/invoice builders look up;
  blank = that line is skipped.
"""
from odoo import fields, models


class ElksLodgeSettings(models.Model):
    _inherit = "elks.lodge.settings"

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Currency
    # HUMAN: Currency for the monetary settings below.
    # AI: Defaults to company currency; referenced by currency_field.
    # ──────────────────────────────────────────────────────────────────
    x_event_currency_id = fields.Many2one(
        'res.currency', string="Currency",
        default=lambda self: self.env.company.currency_id,
    )

    # ------------------------------------------------------------------
    # Coordinator fee
    # ------------------------------------------------------------------
    x_coordinator_fee_type = fields.Selection([
        ('fixed', 'Fixed Rate per Event'),
        ('percentage', 'Percentage of Room Income'),
    ], string="Coordinator Fee Type", default='percentage',
        help="How the event coordinator fee is calculated.",
    )
    x_coordinator_fixed_rate = fields.Monetary(
        "Coordinator Fixed Rate", currency_field='x_event_currency_id',
        help="Fixed dollar amount paid per event when fee type is 'Fixed Rate'.",
    )
    x_coordinator_percentage = fields.Float(
        "Coordinator Percentage", default=20.0,
        help="Percent of room rental income paid as coordinator fee.",
    )

    # ------------------------------------------------------------------
    # Guest-driven defaults
    # ------------------------------------------------------------------
    x_guests_per_bartender = fields.Integer(
        "Guests per Bartender", default=75,
        help="One bartender per this many guests. Drives the Bartenders "
             "field on the event (editable).",
    )
    x_max_bars = fields.Integer(
        "Bar Locations Available", default=2,
        help="How many physical bar locations the lodge has. The public "
             "booking form limits 'Number of Bars' to this many.",
    )
    # Labor — estimated hours used to seed a quote; the per-hour CHARGE is the
    # Sales Price of the event-labor and cleaning products.
    x_default_event_hours = fields.Float(
        "Default Event-Staff Hours", default=8.0,
        help="Estimated event-staff hours used to start a quote.",
    )
    x_default_cleaning_hours = fields.Float(
        "Default Cleaning Hours", default=2.0,
        help="Estimated custodial/cleaning hours used to start a quote.",
    )
    x_per_plate_cost = fields.Monetary(
        "Per-Plate Cost", currency_field='x_event_currency_id',
        help="Internal kitchen/food cost per guest.",
    )
    x_per_plate_charge = fields.Monetary(
        "Per-Plate Charge", currency_field='x_event_currency_id',
        help="Amount billed to the customer per plate/guest.",
    )

    # ------------------------------------------------------------------
    # Pricing policy
    # ------------------------------------------------------------------
    x_member_discount_pct = fields.Float(
        "Member Discount %",
        help="Discount applied to member rentals.",
    )
    x_deposit_pct = fields.Float(
        "Deposit %", default=50.0,
        help="Percent of the total due as a deposit to secure the date.",
    )
    x_balance_due_days_before = fields.Integer(
        "Balance Due (days before event)", default=21,
    )
    x_event_tax_id = fields.Many2one(
        'account.tax', string="Event Tax",
        domain="[('type_tax_use', '=', 'sale')]",
        help="Tax shown on event documents (may be 0%; UBI may change this).",
    )

    # ------------------------------------------------------------------
    # Terms & documents
    # ------------------------------------------------------------------
    x_event_terms_url = fields.Char(
        "Rental Agreement URL",
        help="Link to the downloadable Rental Use Agreement / Terms.",
    )
    x_event_terms_label = fields.Char(
        "Terms Link Label",
        default="Rental Use Agreement (Terms & Conditions)",
    )
    x_event_floor_plan = fields.Binary(
        "Lodge Floor Plan",
        help="A picture/map of the lodge shown on the public booking form "
             "above the room choices. Upload a PNG/JPG.",
    )
    x_event_calendar_user_id = fields.Many2one(
        'res.users', string="Events Calendar User",
        help="Approved events publish onto THIS user's calendar. Set the Elks "
             "Calendar publication's 'Source User Calendar' to the same user "
             "so approved events populate the printed calendar automatically.",
    )

    # ------------------------------------------------------------------
    # Default products (the items added to event quotes / invoices)
    # ------------------------------------------------------------------
    x_facility_product_id = fields.Many2one(
        'product.product', string="Facility (Invoice) Product",
        help="Single line product used on the customer's Final invoice.")
    x_deposit_product_id = fields.Many2one(
        'product.product', string="Deposit Product",
        help="Single line product used on the Deposit invoice.")
    x_bar_product_id = fields.Many2one('product.product', string="Bar Product")
    x_bartender_product_id = fields.Many2one(
        'product.product', string="Bartender Product")
    x_cleaning_product_id = fields.Many2one(
        'product.product', string="Cleaning Product")
    x_garbage_product_id = fields.Many2one(
        'product.product', string="Garbage Product")
    x_linen_product_id = fields.Many2one(
        'product.product', string="Linen Product")
    x_catering_product_id = fields.Many2one(
        'product.product', string="Catering Product")
    x_coordinator_product_id = fields.Many2one(
        'product.product', string="Coordinator Fee Product")
    x_event_labor_product_id = fields.Many2one(
        'product.product', string="Event Staff (per hour) Product",
        help="Per-hour charge for event staff; quote line = hours x its price.")
    x_insurance_product_id = fields.Many2one(
        'product.product', string="Insurance Product")
    x_default_quote_product_ids = fields.Many2many(
        'product.product', 'elks_settings_default_quote_product_rel',
        'settings_id', 'product_id',
        string="Default Quote Products",
        help="Products automatically added to every new event quote.")

    # ------------------------------------------------------------------
    # Checklist
    # ------------------------------------------------------------------
    x_event_checklist_template_id = fields.Many2one(
        'elks.event.checklist.template', string="Default Checklist Template",
        help="Template used to generate event subtasks. Falls back to the "
             "template flagged 'Default' if left blank.")

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Default events project
    # HUMAN: Where new event requests (web + manual default) are filed.
    # AI: m2o project.project; controller _get_event_project prefers this,
    #     else the current Elk-Year project. Defaults to project_event_bookings.
    # ──────────────────────────────────────────────────────────────────
    x_event_project_id = fields.Many2one(
        'project.project', string="Default Events Project",
        domain="[('x_is_event_project', '=', True)]",
        default=lambda self: self.env.ref(
            'elksevent.project_event_bookings', raise_if_not_found=False),
        help="The living 'Events' project new requests are filed into. "
             "Leave blank to use the current Elk-Year project.")
