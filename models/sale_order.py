# -*- coding: utf-8 -*-
"""Link a sale.order (the staff-only itemized quote) to an event.

HUMAN
-----
Every event booking has one "quote" behind the scenes — an ordinary Odoo
sales quotation that staff use to itemize rooms, bar, catering, linens, etc.
The customer never sees this quote; it stays in Draft and is only used to add
up the real cost. Each quote line remembers whether it was built automatically
from the event (so a rebuild can refresh those without wiping lines a staffer
added by hand).

AI
--
- Extends `sale.order` (adds `x_event_task_id`) and `sale.order.line`
  (adds `x_event_source`).
- `x_event_source` tags auto-generated lines. Format strings used by the
  builder in `project.task._sync_quote_lines`: 'room:<bookingId>', 'bartender',
  'catering', 'default:<productId>', 'coordinator'. Blank = manual line.
- Quotes are deliberately never confirmed; a global ir.rule
  (`rule_sale_order_event_quotes_internal_only`) hides any SO with
  `x_event_task_id` set from share (portal/public) users.
- `event_ap_breakdown()` is consumed by the AP GL breakout QWeb report; it
  groups non-display lines by `product.product_tmpl_id.x_elks_income_account_id`.
"""
from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Event link
    # HUMAN: Connects this quotation back to its event booking.
    # AI: Indexed m2o to project.task; mirror of project.task.x_sale_order_id.
    # ──────────────────────────────────────────────────────────────────
    x_event_task_id = fields.Many2one(
        'project.task', string="Event", copy=False, index=True,
        help="The event booking this quote itemizes (staff-only).",
    )

    # ──────────────────────────────────────────────────────────────────
    # SECTION: AP GL breakout
    # HUMAN: Splits the quote total by chart-of-accounts line so Accounts
    #        Payable can see how the incoming money should be booked.
    # AI: Pure read helper for report_event_ap_breakout. Returns a list of
    #     {account, lines[], subtotal} sorted by account label. Lines with no
    #     FRS account fall under 'Unassigned'. No DB writes.
    # ──────────────────────────────────────────────────────────────────
    def event_ap_breakdown(self):
        """Group quote lines by their product's FRS income account."""
        self.ensure_one()
        groups = {}
        for line in self.order_line.filtered(lambda l: not l.display_type):
            acct = line.product_id.product_tmpl_id.x_elks_income_account_id
            key = acct.id if acct else 0
            g = groups.setdefault(key, {
                'account': acct.display_name if acct else 'Unassigned',
                'lines': [],
                'subtotal': 0.0,
            })
            g['lines'].append({
                'name': line.name,
                'qty': line.product_uom_qty,
                'price': line.price_unit,
                'subtotal': line.price_subtotal,
            })
            g['subtotal'] += line.price_subtotal
        return sorted(groups.values(), key=lambda x: x['account'])


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    # ──────────────────────────────────────────────────────────────────
    # SECTION: Auto-line marker
    # HUMAN: Flags lines the system generated from the event, so rebuilding
    #        the quote refreshes them without touching hand-added lines.
    # AI: Set/cleared only by project.task._sync_quote_lines &
    #     action_add_coordinator_fee. Filter with `.filtered('x_event_source')`.
    # ──────────────────────────────────────────────────────────────────
    x_event_source = fields.Char(
        "Event Auto-Line Source", copy=False,
        help="Set on lines auto-generated from the event (room, bartender, "
             "catering, coordinator, default). Blank on manually added lines, "
             "which the rebuild preserves.",
    )
