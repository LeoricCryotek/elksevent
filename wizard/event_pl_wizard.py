# -*- coding: utf-8 -*-
"""Event P&L Report Wizard.

HUMAN
-----
Shows whether events made or lost money. Pick a date range (or one specific
event) and it builds a Profit & Loss: income for each event versus its costs
(internal cost lines plus any linked purchase orders), with the lodge header.

AI
--
- TransientModel `elks.event.pl.wizard`; report actions
  action_report_event_pl (html) / _pdf.
- `_get_report_data`: when event_id is set, dates are ignored (so dateless/
  tentative events still report). Income now prefers evt.x_total_billed (which
  is quote-aware) and falls back to room_income+coordinator.
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
        if self.event_id:
            # A specific event was chosen — report on it regardless of date
            # (some events, e.g. tentative ones, have no event date set).
            domain = [('id', '=', self.event_id.id)]
        else:
            domain = [
                ('x_is_event', '=', True),
                ('x_event_date', '>=', self.date_from),
                ('x_event_date', '<=', self.date_to),
            ]

        events = self.env['project.task'].search(domain, order='x_event_date')
        # The per-event P&L math lives on project.task so the wizard report and
        # the per-event Print report stay in sync.
        return events._get_pl_report_data()
