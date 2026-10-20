"""Internal coordinator/supplies fund register, separate from accounting GL.

Posted entries are immutable. PO reservations affect availability rather than
spend; settling a PO releases the matching commitment. Payroll posting creates
one flat coordinator share on the employee timecard, not a second event cost.
"""
from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class EventFund(models.Model):
    _name = 'elks.event.fund'
    _description = 'Coordinator / Event Supplies Fund'
    _check_company_auto = True

    name = fields.Char(default='Coordinator / Event Supplies Fund', required=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    entry_ids = fields.One2many('elks.event.fund.entry', 'fund_id', string='Register')
    purchase_ids = fields.One2many('purchase.order', 'x_event_fund_id')
    income = fields.Monetary(compute='_compute_totals', compute_sudo=True)
    spending = fields.Monetary(compute='_compute_totals', compute_sudo=True)
    balance = fields.Monetary(compute='_compute_totals', compute_sudo=True, string='Posted Balance')
    committed = fields.Monetary(compute='_compute_totals', compute_sudo=True, string='Open PO Commitments')
    available = fields.Monetary(compute='_compute_totals', compute_sudo=True, string='Available to Allocate')
    _company_unique = models.Constraint('UNIQUE(company_id)', 'One coordinator fund per company.')

    @api.model
    def _company_fund(self, company):
        fund = self.sudo().search([('company_id', '=', company.id)], limit=1)
        if not fund:
            fund = self.sudo().create({'company_id': company.id})
        return fund

    @api.depends('entry_ids.state', 'entry_ids.signed_amount',
                 'purchase_ids.state', 'purchase_ids.amount_total')
    def _compute_totals(self):
        for fund in self:
            posted = fund.entry_ids.filtered(lambda e: e.state == 'posted')
            fund.income = sum(e.signed_amount for e in posted if e.signed_amount > 0)
            fund.spending = -sum(e.signed_amount for e in posted if e.signed_amount < 0)
            fund.balance = fund.income - fund.spending
            commitments = 0.0
            for po in fund.purchase_ids.filtered(lambda p: p.state in ('purchase', 'done')):
                settled = -sum(posted.filtered(lambda e: e.purchase_id == po).mapped('signed_amount'))
                commitments += max(po.amount_total - settled, 0.0)
            fund.committed = commitments
            fund.available = fund.balance - commitments

    @api.model
    def action_open_register(self):
        fund = self._company_fund(self.env.company)
        return {'type': 'ir.actions.act_window', 'name': fund.name,
                'res_model': self._name, 'res_id': fund.id, 'view_mode': 'form'}

    def action_new_entry(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Fund Entry'),
                'res_model': 'elks.event.fund.entry', 'view_mode': 'form',
                'target': 'current', 'context': {'default_fund_id': self.id}}

    def action_open_allocations(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Event Allocations'),
                'res_model': 'project.task', 'view_mode': 'list,form',
                'domain': [('company_id', '=', self.company_id.id),
                           ('x_is_event', '=', True), ('x_coordinator_paid_from_event', '=', False)],
                'views': [(self.env.ref('elksevent.view_event_fund_allocations').id, 'list'), (False, 'form')]}


class EventFundEntry(models.Model):
    _name = 'elks.event.fund.entry'
    _description = 'Coordinator Fund Entry'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'
    _check_company_auto = True

    name = fields.Char(string='Description', required=True, tracking=True)
    fund_id = fields.Many2one('elks.event.fund', required=True, ondelete='restrict', check_company=True)
    company_id = fields.Many2one(related='fund_id.company_id', store=True)
    currency_id = fields.Many2one(related='fund_id.currency_id')
    date = fields.Date(required=True, default=fields.Date.context_today)
    kind = fields.Selection([('contribution', 'Event Contribution'),
        ('opening', 'Opening Balance / Other Funding'), ('expense', 'Purchase / Expense'),
        ('payroll', 'Coordinator Timecard Allocation')], required=True, default='expense', tracking=True)
    amount = fields.Monetary(required=True, tracking=True)
    signed_amount = fields.Monetary(compute='_compute_signed', store=True)
    state = fields.Selection([('draft', 'Draft'), ('posted', 'Posted')], default='draft', required=True, tracking=True)
    event_id = fields.Many2one('project.task', domain="[('x_is_event', '=', True)]", check_company=True)
    contribution_event_id = fields.Many2one('project.task', copy=False, readonly=True, check_company=True)
    employee_id = fields.Many2one('hr.employee', check_company=True)
    partner_id = fields.Many2one('res.partner', string='Payee / Vendor', check_company=True)
    purchase_id = fields.Many2one('purchase.order', string='Purchase Order', check_company=True)
    reference = fields.Char(string='Check / Receipt / Payment Reference')
    notes = fields.Text()
    attendance_id = fields.Many2one('hr.attendance', readonly=True, copy=False, ondelete='restrict')
    posted_by = fields.Many2one('res.users', readonly=True, copy=False)
    posted_on = fields.Datetime(readonly=True, copy=False)
    _contribution_unique = models.Constraint('UNIQUE(contribution_event_id)', 'This event contribution has already been posted.')

    @api.depends('kind', 'amount')
    def _compute_signed(self):
        for entry in self:
            entry.signed_amount = entry.amount if entry.kind in ('contribution', 'opening') else -entry.amount

    @api.constrains('amount')
    def _check_amount(self):
        for entry in self:
            if entry.amount <= 0:
                raise ValidationError(_('Enter an amount greater than zero.'))

    @api.model_create_multi
    def create(self, values_list):
        if any(v.get('state', 'draft') != 'draft' or v.get('contribution_event_id')
               or v.get('attendance_id') for v in values_list):
            raise UserError(_('Create a draft and use Post to record the fund entry.'))
        return super().create(values_list)

    def write(self, vals):
        protected = {'name', 'fund_id', 'date', 'kind', 'amount', 'state', 'event_id',
                     'employee_id', 'partner_id', 'purchase_id', 'reference', 'notes',
                     'contribution_event_id', 'attendance_id', 'posted_by', 'posted_on'}
        if protected.intersection(vals):
            if any(e.state == 'posted' for e in self):
                raise UserError(_('Posted register entries cannot be changed. Record a documented correction separately.'))
            if {'state', 'contribution_event_id', 'attendance_id', 'posted_by', 'posted_on'}.intersection(vals):
                raise UserError(_('Use Post to finalize this entry.'))
        return super().write(vals)

    def unlink(self):
        if any(e.state == 'posted' for e in self):
            raise UserError(_('Posted fund entries cannot be deleted.'))
        return super().unlink()

    def _validate_event_contribution(self):
        event = self.event_id
        if not event or not event.x_is_event or event.x_coordinator_paid_from_event:
            raise UserError(_('Select an event whose coordinator fee is allocated to the pool.'))
        if event.x_coordinator_fee_paid or any(event.x_attendance_ids.mapped('x_coordinator_fee_share')):
            raise UserError(_('This event already has a coordinator timecard payout.'))
        if self.currency_id.compare_amounts(self.amount, event.x_coordinator_to_supplies):
            raise UserError(_('The contribution must equal this event’s pooled coordinator fee.'))
        owed = event.x_agreed_grand if event.x_price_locked else event.x_event_grand_total
        if owed <= 0 or event.x_balance_remaining > 0 or self.currency_id.compare_amounts(event.x_total_paid, owed) < 0:
            raise UserError(_('The event must be fully paid before its contribution is posted. Write-offs do not count as receipts.'))
        return event.id

    def action_post(self):
        self.ensure_one()
        if not self.env.user.has_group('elksevent.group_event_coordinator'):
            raise UserError(_('Only event coordinators may post fund entries.'))
        self.check_access('write')
        # Lock this draft before resolving its fund, then serialize fund posting.
        self.env.cr.execute('SELECT id FROM elks_event_fund_entry WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset()
        self.env.cr.execute('SELECT id FROM elks_event_fund WHERE id = %s FOR UPDATE', [self.fund_id.id])
        self.invalidate_recordset()
        self.fund_id.invalidate_recordset()
        if self.state == 'posted':
            return True
        if self.amount <= 0:
            raise UserError(_('Enter an amount greater than zero.'))
        vals = {'state': 'posted', 'posted_by': self.env.uid, 'posted_on': fields.Datetime.now()}
        if self.kind == 'contribution':
            vals['contribution_event_id'] = self._validate_event_contribution()
        if self.purchase_id:
            if self.kind != 'expense' or self.purchase_id.x_event_fund_id != self.fund_id:
                raise UserError(_('Choose a Purchase / Expense entry and a PO charged to this fund.'))
            if self.purchase_id.state not in ('purchase', 'done'):
                raise UserError(_('Confirm the PO before recording its fund payment.'))
        if self.kind in ('expense', 'payroll'):
            available = self.fund_id.available
            if self.purchase_id:
                settled = -sum(self.fund_id.entry_ids.filtered(
                    lambda e: e.state == 'posted' and e.purchase_id == self.purchase_id).mapped('signed_amount'))
                remaining = max(self.purchase_id.amount_total - settled, 0.0)
                if self.currency_id.compare_amounts(self.amount, remaining) > 0:
                    raise UserError(_('This amount exceeds the unpaid commitment on the selected PO.'))
                available += remaining
            if self.currency_id.compare_amounts(self.amount, available) > 0:
                raise UserError(_('The fund does not have enough uncommitted money for this entry.'))
        if self.kind == 'payroll':
            if not self.employee_id:
                raise UserError(_('Select the coordinator’s employee record.'))
            # A pool payout is independent of an event's already allocated fee.
            # Do not tag it to an event and charge that event a second time.
            if self.employee_id.department_id.name == 'Volunteers':
                raise UserError(_('This employee is in Volunteers, which payroll excludes. Move them to a payroll-eligible department before posting a paid coordinator fee.'))
            if 'elksattendance.timecard.cron' in self.env:
                tz = self.env['elksattendance.timecard.cron'].with_company(self.company_id)._lodge_tz()
            else:
                tz = pytz.timezone(self.employee_id.resource_calendar_id.tz or self.env.user.tz or 'UTC')
            day_start = tz.localize(datetime.combine(self.date, time.min)).astimezone(pytz.UTC).replace(tzinfo=None)
            day_end = tz.localize(datetime.combine(self.date + timedelta(days=1), time.min)).astimezone(pytz.UTC).replace(tzinfo=None)
            Att = self.env['hr.attendance'].sudo()
            shifts = Att.search([
                ('employee_id', '=', self.employee_id.id), ('check_in', '<', day_end),
                '|', ('check_out', '=', False), ('check_out', '>', day_start)], order='check_in')
            when = day_start
            for shift in shifts:
                if shift.check_in <= when and (not shift.check_out or shift.check_out > when):
                    if not shift.check_out:
                        raise UserError(_('Close the employee’s open timeclock shift before allocating this fee.'))
                    when = shift.check_out
            if when >= day_end:
                raise UserError(_('No free timeclock instant exists on this date. Choose another payroll date.'))
            att = Att.create({
                'employee_id': self.employee_id.id, 'check_in': when, 'check_out': when,
                'x_coordinator_fee_share': self.amount,
            })
            vals['attendance_id'] = att.id
        super(EventFundEntry, self).write(vals)
        self.message_post(body=_('Fund entry posted. Payroll entries allocate to a timecard; they do not send payment.'))
        return True


class EventFundTask(models.Model):
    _inherit = 'project.task'

    x_coordinator_fund_entry_ids = fields.One2many(
        'elks.event.fund.entry', 'contribution_event_id', groups='elksevent.group_event_coordinator')
    x_coordinator_fund_posted = fields.Boolean(compute='_compute_fund_posted',
        groups='elksevent.group_event_coordinator')

    @api.depends('x_coordinator_fund_entry_ids.state')
    def _compute_fund_posted(self):
        for event in self:
            event.x_coordinator_fund_posted = any(
                entry.state == 'posted' for entry in event.x_coordinator_fund_entry_ids)

    def _compute_event_supplies_fund(self):
        for event in self:
            fund = self.env['elks.event.fund']._company_fund(event.company_id or self.env.company)
            event.x_event_supplies_fund_total = fund.available

    def action_open_coordinator_fund(self):
        self.ensure_one()
        fund = self.env['elks.event.fund']._company_fund(self.company_id or self.env.company)
        return {'type': 'ir.actions.act_window', 'res_model': fund._name,
                'res_id': fund.id, 'view_mode': 'form', 'name': fund.name}

    def action_contribute_coordinator_fund(self):
        self.ensure_one()
        fund = self.env['elks.event.fund']._company_fund(self.company_id or self.env.company)
        return {'type': 'ir.actions.act_window', 'name': _('Post Event Contribution'),
                'res_model': 'elks.event.fund.entry', 'view_mode': 'form',
                'context': {'default_fund_id': fund.id, 'default_kind': 'contribution',
                            'default_event_id': self.id, 'default_amount': self.x_coordinator_to_supplies,
                            'default_name': _('Coordinator allocation: %s', self.name)}}

    def action_pay_coordinator(self):
        for event in self:
            if self.env['elks.event.fund.entry'].sudo().search_count([
                    ('contribution_event_id', '=', event.id), ('state', '=', 'posted')]):
                raise UserError(_('This event fee has been transferred to the pool. Use the fund register to allocate a coordinator timecard payment instead.'))
        return super().action_pay_coordinator()


class EventFundPurchase(models.Model):
    _inherit = 'purchase.order'

    x_event_fund_id = fields.Many2one('elks.event.fund', string='Charge to Coordinator Fund',
        check_company=True, tracking=True,
        help='Confirmed POs reserve this amount in the fund. Record the actual payment in the fund register against this PO to settle the reservation. Does not add PO costs to event P&L.')

    @api.constrains('x_event_fund_id', 'amount_total', 'state', 'currency_id')
    def _check_fund_commitment(self):
        for po in self:
            payments = self.env['elks.event.fund.entry'].sudo().search([
                ('purchase_id', '=', po.id), ('state', '=', 'posted')])
            if any(entry.fund_id != po.x_event_fund_id for entry in payments):
                raise ValidationError(_('A PO with posted fund payments cannot be moved to another fund.'))
        funds = self.mapped('x_event_fund_id').sorted('id')
        for fund in funds:
            self.env.cr.execute('SELECT id FROM elks_event_fund WHERE id = %s FOR UPDATE', [fund.id])
            fund.invalidate_recordset()
            if any(p.currency_id != fund.currency_id for p in self if p.x_event_fund_id == fund):
                raise ValidationError(_('Fund POs must use the fund’s currency.'))
            if fund.currency_id.compare_amounts(fund.available, 0) < 0:
                raise ValidationError(_('This PO would exceed the coordinator fund’s available balance.'))

