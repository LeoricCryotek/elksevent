# -*- coding: utf-8 -*-
"""Event P&L Report Wizard.

Allows selecting a date range and optionally a single event, then
generates a Profit & Loss report showing income (room bookings)
vs. expenses (cost lines / POs) with Elks lodge header.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class EventPLReportWizard(models.TransientModel):
    _name = "elks.event.pl.wizard"
    _description = "Event P&L Report Wizard"

    date_from = fields.Date(
        "From", required=True,
        default=lambda self: fields.Date.today().replace(day=1),
    )
    date_to = fields.Date(
        "To", required=True,
        default=fields.Date.today,
    )
    event_id = fields.Many2one(
        'project.task', string="Specific Event",
        domain="[('x_is_event', '=', True)]",
        help="Leave blank to include all events in the date range.",
    )

    def action_view_report(self):
        """Open the HTML report."""
        return self.env.ref(
            'elksevent.action_report_event_pl'
        ).report_action(self)

    def action_download_pdf(self):
        """Download the PDF version."""
        return self.env.ref(
            'elksevent.action_report_event_pl_pdf'
        ).report_action(self)

    def _get_report_data(self):
        """Gather P&L data for the report template."""
        self.ensure_one()
        domain = [
            ('x_is_event', '=', True),
            ('x_event_date', '>=', self.date_from),
            ('x_event_date', '<=', self.date_to),
        ]
        if self.event_id:
            domain.append(('id', '=', self.event_id.id))

        events = self.env['project.task'].search(domain, order='x_event_date')

        event_data = []
        grand_income = 0.0
        grand_costs = 0.0

        for evt in events:
            # Income breakdown by room
            room_lines = []
            for rb in evt.x_room_booking_ids:
                room_lines.append({
                    'name': rb.room_id.name,
                    'rate': rb.rate,
                    'cleaning': rb.cleaning_fee,
                    'service': rb.service_fee,
                    'subtotal': rb.subtotal,
                })

            # Cost breakdown
            cost_lines = []
            for cl in evt.x_cost_line_ids:
                cost_lines.append({
                    'type': dict(cl._fields['cost_type'].selection).get(
                        cl.cost_type, cl.cost_type
                    ),
                    'name': cl.name or '',
                    'quantity': cl.quantity,
                    'unit_cost': cl.unit_cost,
                    'total': cl.total,
                    'coordinator_flag': cl.counts_for_coordinator,
                })

            # PO costs
            po_lines = []
            for po in evt.x_purchase_order_ids.filtered(
                lambda p: p.state != 'cancel'
            ):
                po_lines.append({
                    'name': po.name,
                    'origin': po.origin or '',
                    'total': po.amount_total,
                })

            room_income = evt.x_room_income or 0.0
            coordinator = evt.x_coordinator_fee or 0.0
            total_income = room_income + coordinator
            total_costs = evt.x_total_costs or 0.0
            po_total = sum(p['total'] for p in po_lines)
            total_expense = total_costs + po_total
            profit = total_income - total_expense

            grand_income += total_income
            grand_costs += total_expense

            event_data.append({
                'event': evt,
                'room_lines': room_lines,
                'cost_lines': cost_lines,
                'po_lines': po_lines,
                'room_income': room_income,
                'coordinator_fee': coordinator,
                'total_income': total_income,
                'total_costs': total_costs,
                'po_total': po_total,
                'total_expense': total_expense,
                'profit': profit,
            })

        return {
            'events': event_data,
            'event_count': len(event_data),
            'grand_income': grand_income,
            'grand_costs': grand_costs,
            'grand_profit': grand_income - grand_costs,
        }
