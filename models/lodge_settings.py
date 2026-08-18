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
    # Labor rates (per hour, the lodge's COST) + flat service fees per area.
    # These drive the auto-generated Event Costs service lines: the customer
    # charge = hours x rate x 1.15 (15% labor markup) + the service fee, and
    # the line COGS = hours x rate.
    x_event_staff_rate = fields.Monetary(
        "Event-Staff Rate / hr", currency_field='x_event_currency_id')
    x_event_service_fee = fields.Monetary(
        "Event Service Fee", currency_field='x_event_currency_id')
    x_bartender_rate = fields.Monetary(
        "Bartender Rate / hr", currency_field='x_event_currency_id')
    x_bar_service_fee = fields.Monetary(
        "Bar Service Fee", currency_field='x_event_currency_id')
    x_kitchen_rate = fields.Monetary(
        "Kitchen Rate / hr", currency_field='x_event_currency_id',
        help="Legacy flat kitchen rate. Cooks and kitchen support now use "
             "their own rates below.")
    x_cook_rate = fields.Monetary(
        "Cook Rate / hr", currency_field='x_event_currency_id')
    x_kitchen_support_rate = fields.Monetary(
        "Kitchen Support Rate / hr", currency_field='x_event_currency_id')
    x_kitchen_service_fee = fields.Monetary(
        "Kitchen Service Fee", currency_field='x_event_currency_id')
    x_custodial_rate = fields.Monetary(
        "Custodial Rate / hr", currency_field='x_event_currency_id')
    x_custodial_service_fee = fields.Monetary(
        "Custodial Service Fee", currency_field='x_event_currency_id')
    # Department managers who receive the event Call-Out / quote request.
    x_bar_manager_id = fields.Many2one(
        'res.partner', string="Bar Manager",
        help="Emailed the Bar call-out / quote request for an event.")
    x_kitchen_manager_id = fields.Many2one(
        'res.partner', string="Kitchen Manager",
        help="Emailed the Kitchen call-out / quote request for an event.")
    x_custodial_manager_id = fields.Many2one(
        'res.partner', string="Custodial Lead",
        help="Emailed the Custodial call-out for an event.")
    x_labor_markup_pct = fields.Float(
        "Labor Markup %", default=15.0,
        help="Markup added to labor lines (charge = cost x (1 + this%)).")

    x_marketing_signage_fee = fields.Monetary(
        "Marketing Signage Use Fee", currency_field='x_event_currency_id',
        help="Default fee for the 'Marketing Signage Use' Event Cost.",
    )
    x_exclusive_use_fee = fields.Monetary(
        "Exclusive Use Fee", currency_field='x_event_currency_id',
        help="Default fee for the 'Exclusive Use' Event Cost.",
    )
    x_event_insurance_fee = fields.Monetary(
        "Event Insurance Fee", currency_field='x_event_currency_id',
        help="Default insurance charge auto-added to every non-lodge event. "
             "Adjustable per event on the Event Costs list.",
    )
    x_customer_survey_id = fields.Many2one(
        'survey.survey', string="Customer Feedback Survey",
        help="Survey emailed to the customer when their event moves to the "
             "After Action stage. When they finish it, the coordinator is "
             "notified.",
    )
    x_insurance_program_url = fields.Char(
        "Rental Insurance URL",
        default="https://insure.kandkinsurance.com/sites/ElksLodge/Pages/"
                "ELKSInEligibility.aspx",
        help="Link to the K&K / Gallagher Affinity Elks facility-rental "
             "insurance application. Used on the insurance to-do and the "
             "printed worksheet.",
    )

    # ------------------------------------------------------------------
    # Event invoice / billing options (deposit + final invoices)
    # ------------------------------------------------------------------
    x_invoice_terms_acceptance = fields.Boolean(
        "Payment-Is-Acceptance Clause", default=True,
        help="Print a clause on event invoices stating that paying any part "
             "(including the deposit) accepts the Rental Terms & Conditions.")
    x_invoice_terms_embed = fields.Boolean(
        "Embed Full Terms on Invoices", default=True,
        help="Include the full current Terms & Conditions text (from the "
             "/event-terms page) on the invoice itself.")
    x_invoice_terms_link = fields.Boolean(
        "Link to Terms on Invoices", default=True,
        help="Include a clickable link to the live Terms & Conditions page.")
    x_invoice_attach_agreement = fields.Boolean(
        "Attach Facility Agreement to Invoices", default=True,
        help="On deposit/final invoice creation, attach the signable Facility "
             "Usage Agreement PDF (full T&C + signature lines) to the invoice.")
    x_invoice_signature_line = fields.Boolean(
        "Signature Line on Invoices", default=False,
        help="Add a Host / Elks-representative signature + date line on the "
             "invoice itself (in addition to the attached agreement).")
    x_invoice_show_discount = fields.Boolean(
        "Show Member Discount Line on Invoices", default=True,
        help="Bill the room at its retail rate and show the member / "
             "Celebration-of-Life discount as its own line, so the invoice "
             "reflects the discount (retail -> actual).")

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
    x_ubi_tax_pct = fields.Float(
        "UBI / Property Tax %",
        help="Percent reserved for property tax on NON-member room rental "
             "income (Unrelated Business Income). Assessed on the room rental "
             "profit of non-member events and shown on the internal P&L so the "
             "lodge can set the money aside. Not charged to the customer.",
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
    x_event_contract_terms = fields.Html(
        "Contract Terms Note",
        help="The TERMS & CONDITIONS wording printed on the Facility Usage "
             "Agreement, above the signatures. Edit here to change what the "
             "contract says (the full rules still live on the Terms page the "
             "link points to). Leave blank to use the default wording.",
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
