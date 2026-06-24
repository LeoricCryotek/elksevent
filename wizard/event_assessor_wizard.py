# -*- coding: utf-8 -*-
"""Assessor / UBI income report wizard.

HUMAN
-----
Once a year the property-tax assessor wants a list of the events that count
as taxable business income — i.e. rentals to non-members that aren't lodge
("Elks") events and actually brought in money — with the NET income for each.
Pick a date range, hit View or Download, and this produces exactly that list.

AI
--
- TransientModel `elks.event.assessor.wizard`; report actions
  action_report_event_assessor (html) / _pdf.
- `_get_report_data` filters project.task on x_is_ubi=True within the range;
  income = x_quote_total or x_total_billed, costs = x_total_costs, net = diff.
- x_is_ubi is the computed flag on project.task (non-member, non-Elks, net!=0).
"""
from odoo import fields, models


class EventAssessorWizard(models.TransientModel):
    _name = "elks.event.assessor.wizard"
    _description = "Assessor / UBI Income Report Wizard"

    date_from = fields.Date(
        "From", required=True,
        default=lambda self: fields.Date.today().replace(month=1, day=1),
    )
    date_to = fields.Date("To", required=True, default=fields.Date.today)

    def action_view_report(self):
        return self.env.ref(
            'elksevent.action_report_event_assessor').report_action(self)

    def action_download_pdf(self):
        return self.env.ref(
            'elksevent.action_report_event_assessor_pdf').report_action(self)

    def _get_report_data(self):
        self.ensure_one()
        events = self.env['project.task'].search([
            ('x_is_ubi', '=', True),
            ('x_event_date', '>=', self.date_from),
            ('x_event_date', '<=', self.date_to),
        ], order='x_event_date')

        rows = []
        gross = costs = net = 0.0
        for e in events:
            income = e.x_quote_total or e.x_total_billed or 0.0
            cost = e.x_total_costs or 0.0
            n = income - cost
            rows.append({
                'event': e,
                'date': e.x_event_date,
                'name': e.name,
                'customer': e.x_customer_name or (
                    e.partner_id.name if e.partner_id else ''),
                'rooms': ", ".join(
                    e.x_room_booking_ids.mapped('room_id.name')),
                'income': income,
                'cost': cost,
                'net': n,
            })
            gross += income
            costs += cost
            net += n

        return {
            'rows': rows,
            'count': len(rows),
            'grand_income': gross,
            'grand_costs': costs,
            'grand_net': net,
        }
