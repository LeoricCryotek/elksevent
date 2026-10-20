"""Allocate event proceeds to a charity without changing operating P&L.

Disbursement is a distribution of proceeds, not a second event expense. Keep
operating profit intact, reserve pooled coordinator fees, and show the charity
allocation and lodge retention separately. No payment or GL entry is created.
"""
import math

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, UserError


class EventCharityDisbursement(models.Model):
    _inherit = 'project.task'

    x_charity_disbursement_enabled = fields.Boolean(
        'Allocate Proceeds to Charity', default=False, tracking=True)
    x_charity_beneficiary_id = fields.Many2one(
        'res.partner', string='Charity / Beneficiary', tracking=True,
        help='Select or create the charity’s contact record. This is the beneficiary of the proceeds allocation.')
    x_charity_disbursement_method = fields.Selection([
        ('all', 'All Available Proceeds'), ('percent', 'Percentage of Proceeds'),
        ('fixed', 'Fixed Amount')], string='Allocation Method',
        default='percent', required=True, tracking=True)
    x_charity_disbursement_percent = fields.Float(
        'Charity Percentage', default=100.0, tracking=True,
        help='Percentage of positive event profit after expenses and the coordinator fee reserved for the supplies fund. Must be between 0 and 100.')
    x_charity_disbursement_fixed = fields.Monetary(
        'Requested Fixed Charity Amount', currency_field='x_currency_id', tracking=True,
        help='Requested fixed donation. The actual charity allocation is capped at positive proceeds remaining after event costs and the coordinator supplies-fund reserve.')
    x_charity_disbursement_notes = fields.Text(
        'Charity Notes / Purpose', tracking=True,
        help='Purpose, beneficiary instructions, approval details and any restrictions on the proceeds.')
    x_charity_available_proceeds = fields.Monetary(
        'Available Event Proceeds', currency_field='x_currency_id',
        compute='_compute_charity_disbursement',
        help='Formula: max(Event Profit / Loss − Coordinator Fee Pooled to Supplies, 0). Source: Financials → event P&L and Customer Pricing → Coordinator Pay. Calculated before the charity allocation.')
    x_charity_disbursement_amount = fields.Monetary(
        'Allocated to Charity', currency_field='x_currency_id',
        compute='_compute_charity_disbursement',
        help='Formula: all available proceeds, proceeds × charity percentage / 100, or the entered fixed amount capped at available proceeds. Zero when Allocate Proceeds to Charity is off. This is an allocation, not proof of payment.')
    x_charity_retained_net = fields.Monetary(
        'Net Retained After Charity', currency_field='x_currency_id',
        compute='_compute_charity_disbursement',
        help='Formula: Event Profit / Loss − Allocated to Charity. Source: Financials → event P&L and Charity Disbursement. Includes the amount reserved for the coordinator supplies fund.')
    x_charity_disbursement_shortfall = fields.Monetary(
        'Unallocated Fixed Amount', currency_field='x_currency_id',
        compute='_compute_charity_disbursement',
        help='Formula: max(Requested Fixed Charity Amount − actual charity allocation, 0). This portion is not allocated because the event has insufficient proceeds.')

    x_charity_certificate_message = fields.Text(
        'Certificate Message', tracking=True,
        help='Optional recipient-facing dedication printed on the certificate. Internal Charity Notes are not printed on the certificate.')

    def _validate_charity_certificate(self):
        self.ensure_one()
        if not self.env.user.has_group('elksevent.group_event_coordinator'):
            raise UserError(_('Only event coordinators may print this certificate.'))
        if not self.x_is_event or not self.x_charity_disbursement_enabled or not self.x_charity_beneficiary_id:
            raise UserError(_('Enable Charity Disbursement and select a beneficiary first.'))
        if self.x_charity_disbursement_amount <= 0:
            raise UserError(_('There are no proceeds available for a charity certificate after event costs and reserves.'))
        return True

    def action_print_charity_certificate(self):
        for event in self:
            event._validate_charity_certificate()
        return self.env.ref('elksevent.action_report_event_charity_certificate').report_action(self)

    @api.depends('x_charity_disbursement_enabled', 'x_charity_disbursement_method',
                 'x_charity_disbursement_percent', 'x_charity_disbursement_fixed',
                 'x_pl_net_total', 'x_coordinator_to_supplies', 'x_currency_id')
    def _compute_charity_disbursement(self):
        for event in self:
            profit = event.x_pl_net_total or 0.0
            currency = event.x_currency_id or event.company_id.currency_id or self.env.company.currency_id
            proceeds = currency.round(max(profit - (event.x_coordinator_to_supplies or 0.0), 0.0))
            amount = 0.0
            if event.x_charity_disbursement_enabled:
                if event.x_charity_disbursement_method == 'all':
                    amount = proceeds
                elif event.x_charity_disbursement_method == 'percent':
                    amount = proceeds * (event.x_charity_disbursement_percent or 0.0) / 100.0
                else:
                    amount = event.x_charity_disbursement_fixed or 0.0
            requested = currency.round(amount)
            amount = min(max(requested, 0.0), proceeds)
            event.x_charity_available_proceeds = currency.round(proceeds)
            event.x_charity_disbursement_amount = amount
            event.x_charity_retained_net = currency.round(profit - amount)
            event.x_charity_disbursement_shortfall = currency.round(max(requested - amount, 0.0))

    @api.constrains('x_charity_disbursement_enabled', 'x_charity_beneficiary_id',
                    'x_charity_disbursement_method', 'x_charity_disbursement_percent',
                    'x_charity_disbursement_fixed')
    def _check_charity_disbursement(self):
        for event in self:
            if not event.x_charity_disbursement_enabled:
                continue
            if not event.x_charity_beneficiary_id:
                raise ValidationError(_('Select a Charity / Beneficiary for this allocation.'))
            if event.x_charity_disbursement_method == 'percent':
                pct = event.x_charity_disbursement_percent
                if not math.isfinite(pct) or not 0 <= pct <= 100:
                    raise ValidationError(_('Charity percentage must be between 0 and 100.'))
            if event.x_charity_disbursement_method == 'fixed':
                amount = event.x_charity_disbursement_fixed
                if not math.isfinite(amount) or amount < 0:
                    raise ValidationError(_('The fixed charity amount cannot be negative or invalid.'))
