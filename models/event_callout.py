# -*- coding: utf-8 -*-
"""Digital department call-out for an event (Bar / Kitchen / Custodial).

HUMAN
-----
When the coordinator needs a department to staff an event, they "send" a
call-out to that department's manager. The manager gets an email + a card in
their website portal (/my) where they review the estimated staff counts and
hours, adjust them, add notes, and CERTIFY — confirming the resources they
will commit. On certification the numbers flow back into the event estimate
(so the quote / P&L reflect them) and the printed call-out PDF is attached to
the event for the After Action.

AI
--
- Model `elks.event.callout`; one per (event_id, department). department in
  bar/kitchen/custodial. state draft -> sent -> certified.
- FIELD_MAP maps this record's generic staffing fields onto the department's
  specific project.task fields; _seed_from_event() copies task->callout and
  _push_to_event() writes callout->task (sudo).
- Portal access via a record rule (manager_partner_id == user.partner_id);
  the controller does sensitive writes (push, attach, notify) with sudo.
"""
import base64
import json

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError

CALLOUT_DEPARTMENTS = [
    ('bar', 'Bar'),
    ('kitchen', 'Kitchen'),
    ('custodial', 'Custodial'),
]

# department -> {callout field: task field}. Only listed fields are synced.
FIELD_MAP = {
    'bar': {
        'num_bars': 'x_num_bars',
        'primary_count': 'x_bartender_count',
        'primary_hours': 'x_plan_bar_hours',
        'drink_requests': 'x_bar_drink_requests',
    },
    'kitchen': {
        'primary_count': 'x_cook_count',
        'primary_hours': 'x_cook_hours',
        'secondary_count': 'x_kitchen_support_count',
        'secondary_hours': 'x_kitchen_support_hours',
    },
    'custodial': {
        'primary_count': 'x_cleaner_count',
        'primary_hours': 'x_cleaner_hours',
        'ready_datetime': 'x_cleanup_datetime',
    },
}


class EventCallout(models.Model):
    _name = "elks.event.callout"
    _description = "Event Department Call-Out"
    _order = "create_date desc, id desc"

    event_id = fields.Many2one(
        'project.task', string="Event", required=True, ondelete='cascade',
        index=True)
    department = fields.Selection(
        CALLOUT_DEPARTMENTS, string="Department", required=True, index=True)
    manager_partner_id = fields.Many2one(
        'res.partner', string="Department Manager", index=True,
        help="The manager who reviews and certifies this call-out in their "
             "portal.")
    state = fields.Selection([
        ('draft', 'Draft'),
        ('sent', 'Sent to Manager'),
        ('certified', 'Certified'),
    ], default='draft', required=True, index=True, tracking=False)

    # Generic staffing estimate (mapped per department via FIELD_MAP)
    num_bars = fields.Integer("Number of Bars")
    primary_count = fields.Integer("Staff Count")
    primary_hours = fields.Float("Hours Each")
    secondary_count = fields.Integer("Support Staff Count")
    secondary_hours = fields.Float("Support Hours Each")
    ready_datetime = fields.Datetime("Building Ready By")
    drink_requests = fields.Text("Drink Requests")
    manager_notes = fields.Text(
        "Manager Notes",
        help="Anything else pertinent — availability, constraints, extra "
             "supplies, sign-off comments.")
    customer_request = fields.Text(
        "Customer Requests", readonly=True,
        help="What the customer requested for this department (read-only). "
             "Seeded from the event; the manager sets the actual staffing.")

    certified_by = fields.Many2one('res.users', string="Certified By",
                                   copy=False)
    certified_on = fields.Datetime("Certified On", copy=False)

    # As-sent plan snapshot + change detection. When the call-out is sent, the
    # department's requested plan (shared schedule + department fields) is
    # snapshotted here (JSON). If the coordinator later edits those on the
    # event, has_plan_changes flags it and plan_changes_html shows old -> new,
    # so the coordinator can decide whether to resend and the manager can see
    # what changed from what was originally asked.
    sent_snapshot = fields.Text("As-Sent Plan (JSON)", copy=False)
    has_plan_changes = fields.Boolean(
        "Plan Changed Since Sent", compute='_compute_plan_changes')
    plan_changes_html = fields.Html(
        "Changes Since Sent", compute='_compute_plan_changes', sanitize=False)

    # Named staff roster (who works, hours, cost) + the department gratuity.
    line_ids = fields.One2many(
        'elks.event.callout.line', 'callout_id', string="Staff Roster")
    gratuity = fields.Monetary(
        "Gratuity (total)", currency_field='currency_id',
        help="Total gratuity for this department's staff. On certification it "
             "feeds the event's gratuity pool (distributed to staff by hours).")
    gratuity_distributed = fields.Monetary(
        "Gratuity Disbursed", currency_field='currency_id',
        compute='_compute_roster_totals', store=False,
        help="Sum of the per-person gratuity shares on the roster.")
    gratuity_remaining = fields.Monetary(
        "Gratuity Left to Assign", currency_field='currency_id',
        compute='_compute_roster_totals', store=False,
        help="Pool total minus what's been assigned per person. Aim for $0.")
    staff_count = fields.Integer(
        "People", compute='_compute_roster_totals', store=False)
    hours_total = fields.Float(
        "Total Hours", compute='_compute_roster_totals', store=False)
    cost_total = fields.Monetary(
        "Total Billed", currency_field='currency_id',
        compute='_compute_roster_totals', store=False)
    raw_cost_total = fields.Monetary(
        "Total Labor Cost", currency_field='currency_id',
        compute='_compute_roster_totals', store=False,
        help="What the lodge pays the staff (COGS) — sum of hours x rate.")
    coverage_total = fields.Monetary(
        "Coverage", currency_field='currency_id',
        compute='_compute_roster_totals', store=False,
        help="Billed minus paid for this department — the margin toward the "
             "bartender pay pool.")

    # Convenience mirrors for the portal templates
    event_name = fields.Char(related='event_id.name', string="Event")
    event_date = fields.Date(related='event_id.x_event_date', string="Date")
    department_label = fields.Char(
        compute='_compute_department_label', string="Department Name")
    currency_id = fields.Many2one(related='event_id.x_currency_id')

    @api.depends('line_ids.hours', 'line_ids.cost', 'line_ids.raw_cost',
                 'line_ids.gratuity_share', 'gratuity')
    def _compute_roster_totals(self):
        for rec in self:
            rec.staff_count = len(rec.line_ids)
            rec.hours_total = sum(rec.line_ids.mapped('hours'))
            rec.cost_total = sum(rec.line_ids.mapped('cost'))
            rec.raw_cost_total = sum(rec.line_ids.mapped('raw_cost'))
            rec.coverage_total = rec.cost_total - rec.raw_cost_total
            rec.gratuity_distributed = sum(
                rec.line_ids.mapped('gratuity_share'))
            rec.gratuity_remaining = (
                (rec.gratuity or 0.0) - rec.gratuity_distributed)

    @api.depends('department')
    def _compute_department_label(self):
        labels = dict(CALLOUT_DEPARTMENTS)
        for rec in self:
            rec.department_label = labels.get(rec.department, '')

    # ── As-sent plan snapshot + change detection ───────────────────────
    # Shared schedule fields every department cares about + the department's
    # own request fields. Each is (event field, label).
    _PLAN_SHARED = [
        ('x_event_date', 'Event date'),
        ('x_requested_entry', 'Requested entry'),
        ('x_requested_setup', 'Setup'),
        ('x_event_start_time_text', 'Event start'),
        ('x_event_end_time_text', 'Event end'),
    ]
    _PLAN_FIELDS = {
        'bar': [
            ('x_num_bars', 'Bars'),
            ('x_need_beer_tub', 'Beer tub'),
            ('x_bar_drink_requests', 'Drink requests'),
            ('x_bar_customer_request', 'Other bar requests'),
        ],
        'kitchen': [
            ('x_catering_details', 'Catering details'),
            ('x_food_menu', 'Menu'),
            ('x_kitchen_customer_request', 'Other food requests'),
        ],
        'custodial': [
            ('x_cleanup_before_datetime', 'Clean prior to event by'),
            ('x_cleanup_datetime', 'Cleanup after event by'),
            ('x_custodial_customer_request', 'Cleaning instructions'),
        ],
    }

    def _plan_specs(self):
        return self._PLAN_SHARED + self._PLAN_FIELDS.get(self.department, [])

    def _fmt_plan_value(self, evt, fname):
        """Render an event field value as a stable display string."""
        val = evt[fname]
        if val in (False, None, ''):
            return ''
        field = evt._fields.get(fname)
        ftype = field.type if field else 'char'
        if ftype == 'datetime':
            return fields.Datetime.context_timestamp(
                evt, val).strftime('%m/%d %I:%M %p')
        if ftype == 'date':
            return val.strftime('%m/%d/%Y')
        if ftype == 'boolean':
            return 'Yes' if val else 'No'
        if ftype in ('float', 'monetary'):
            return '%g' % val
        if ftype == 'integer':
            return str(val)
        return str(val)

    def _current_plan(self):
        self.ensure_one()
        evt = self.event_id
        return [{'key': f, 'label': lbl, 'value': self._fmt_plan_value(evt, f)}
                for f, lbl in self._plan_specs()]

    def _capture_snapshot(self):
        """Store the current requested plan as the as-sent baseline."""
        for rec in self:
            snap = {p['key']: p['value'] for p in rec._current_plan()}
            rec.sent_snapshot = json.dumps(snap)

    def _plan_change_rows(self):
        """List of {label, old, new} where the plan differs from as-sent."""
        self.ensure_one()
        if not self.sent_snapshot:
            return []
        try:
            snap = json.loads(self.sent_snapshot)
        except (ValueError, TypeError):
            return []
        rows = []
        for p in self._current_plan():
            old = snap.get(p['key'], '') or ''
            new = p['value'] or ''
            if old != new:
                rows.append({'label': p['label'], 'old': old, 'new': new})
        return rows

    def _plan_changes_by_key(self):
        """{event field: {label, old, new}} for fields changed since sent."""
        self.ensure_one()
        if not self.sent_snapshot:
            return {}
        try:
            snap = json.loads(self.sent_snapshot)
        except (ValueError, TypeError):
            return {}
        out = {}
        for p in self._current_plan():
            old = snap.get(p['key'], '') or ''
            new = p['value'] or ''
            if old != new:
                out[p['key']] = {'label': p['label'], 'old': old, 'new': new}
        return out

    def _chg(self, key, text):
        """Inline display of a field value; when it changed since the call-out
        was sent, highlight the new value and show the old one struck through
        so the manager sees it whether or not a resend was requested."""
        self.ensure_one()
        text = '' if text in (None, False) else str(text)
        chg = self._plan_changes_by_key().get(key) if self.has_plan_changes \
            else None
        if not chg:
            return escape(text)
        return Markup(
            '<span style="background:#fff3cd;padding:0 3px;border-radius:3px;">'
            '%s</span> <span style="color:#b02a37;font-size:.8em;">(was '
            '<span style="text-decoration:line-through;">%s</span>)</span>'
        ) % (escape(text), escape(chg['old'] or '(blank)'))

    def _render_changes_html(self, rows):
        if not rows:
            return False
        cells = ''.join(
            '<tr>'
            '<td style="padding:2px 8px;font-weight:bold;">%s</td>'
            '<td style="padding:2px 8px;color:#a11;text-decoration:line-through;">'
            '%s</td>'
            '<td style="padding:2px 8px;">&#8594;</td>'
            '<td style="padding:2px 8px;background:#fff3cd;font-weight:bold;">'
            '%s</td></tr>' % (
                r['label'], r['old'] or '(blank)', r['new'] or '(blank)')
            for r in rows)
        return (
            '<table style="border-collapse:collapse;font-size:12px;">'
            '<tr style="color:#666;"><th style="text-align:left;padding:2px 8px;">'
            'Field</th><th style="text-align:left;padding:2px 8px;">Originally'
            '</th><th></th><th style="text-align:left;padding:2px 8px;">Now'
            '</th></tr>%s</table>' % cells)

    @api.depends(
        'sent_snapshot', 'state',
        'event_id.x_event_date', 'event_id.x_requested_entry',
        'event_id.x_requested_setup', 'event_id.x_event_start_time_text',
        'event_id.x_event_end_time_text',
        'event_id.x_num_bars', 'event_id.x_bartender_count',
        'event_id.x_plan_bar_hours', 'event_id.x_need_beer_tub',
        'event_id.x_bar_drink_requests', 'event_id.x_bar_customer_request',
        'event_id.x_cook_count', 'event_id.x_cook_hours',
        'event_id.x_kitchen_support_count', 'event_id.x_kitchen_support_hours',
        'event_id.x_catering_details', 'event_id.x_food_menu',
        'event_id.x_kitchen_customer_request',
        'event_id.x_cleaner_count', 'event_id.x_cleaner_hours',
        'event_id.x_cleanup_before_datetime', 'event_id.x_cleanup_datetime',
        'event_id.x_custodial_customer_request')
    def _compute_plan_changes(self):
        for rec in self:
            rows = (rec._plan_change_rows()
                    if rec.state in ('sent', 'certified') else [])
            rec.has_plan_changes = bool(rows)
            rec.plan_changes_html = rec._render_changes_html(rows)

    # ── Sync helpers ───────────────────────────────────────────────────
    def _seed_from_event(self):
        """Copy the event's current estimate into this call-out."""
        for rec in self:
            evt = rec.event_id
            fmap = FIELD_MAP.get(rec.department, {})
            vals = {co_f: evt[task_f] for co_f, task_f in fmap.items()}
            # One-way: the customer's request note is read-only for the manager.
            cr_field = rec._DEPT_CUSTOMER_REQUEST_FIELD.get(rec.department)
            if cr_field:
                vals['customer_request'] = evt[cr_field] or ''
            if vals:
                rec.write(vals)

    def _push_to_event(self):
        """Write the certified estimate back onto the event (sudo caller)."""
        for rec in self:
            fmap = FIELD_MAP.get(rec.department, {})
            vals = {task_f: rec[co_f] for co_f, task_f in fmap.items()}
            if vals:
                rec.event_id.write(vals)

    def _report_xmlid(self):
        self.ensure_one()
        return 'elksevent.report_event_callout_%s' % self.department

    # ── Certification (called from the portal controller) ──────────────
    def action_certify(self):
        """Manager certifies: lock the numbers onto the event, attach the
        printed call-out to the event, and notify the coordinator."""
        for rec in self:
            if rec.state == 'certified':
                continue
            rec.write({
                'state': 'certified',
                'certified_by': rec.env.uid,
                'certified_on': fields.Datetime.now(),
            })
            # Re-baseline the plan to what the manager certified against, so any
            # later coordinator edit shows as a change for them.
            rec.sudo()._capture_snapshot()
            # Note: the requested counts on the event are the customer's ask and
            # are left untouched; the manager's actual staffing lives in the
            # roster (line_ids) synced below. So we no longer _push_to_event().
            rec.sudo()._sync_roster_and_gratuity()
            # Reflect the certified roster labor in the event's cost lines.
            rec.event_id.sudo()._sync_labor_cost_lines()
            rec.sudo()._attach_certified_pdf()
            rec.sudo()._notify_coordinator_certified()
        return True

    # department -> event gratuity field / planned-roster role
    _DEPT_GRATUITY_FIELD = {'bar': 'x_bar_gratuity',
                            'kitchen': 'x_kitchen_gratuity'}
    _DEPT_ROLE = {'bar': 'bartender', 'kitchen': 'cook',
                  'custodial': 'cleaning'}
    # department -> event field holding the customer's request note
    _DEPT_CUSTOMER_REQUEST_FIELD = {
        'bar': 'x_bar_customer_request',
        'kitchen': 'x_kitchen_customer_request',
        'custodial': 'x_custodial_customer_request',
    }

    def _sync_roster_and_gratuity(self):
        """On certify, push the department gratuity into the event's pool and
        add the named EMPLOYEES to the event's planned staff roster (so their
        clock-in auto-links). Free-text names stay on the call-out only."""
        self.ensure_one()
        evt = self.event_id
        grat_field = self._DEPT_GRATUITY_FIELD.get(self.department)
        if grat_field and self.gratuity:
            evt[grat_field] = self.gratuity
        role = self._DEPT_ROLE.get(self.department)
        if role:
            Assign = self.env['elks.event.staff.assignment']
            have = set(evt.x_staff_assignment_ids.filtered(
                lambda a: a.role == role).mapped('employee_id').ids)
            for line in self.line_ids:
                emp = line.employee_id
                if emp and emp.id not in have:
                    Assign.create({
                        'event_id': evt.id,
                        'employee_id': emp.id,
                        'role': role,
                    })
                    have.add(emp.id)

    def _attach_certified_pdf(self):
        self.ensure_one()
        try:
            pdf, _dummy = self.env['ir.actions.report']._render_qweb_pdf(
                self._report_xmlid(), self.event_id.ids)
            fname = '%s Call-Out (Certified) - %s.pdf' % (
                self.department_label, self.event_id.name or 'Event')
            att = self.env['ir.attachment'].create({
                'name': fname,
                'type': 'binary',
                'datas': base64.b64encode(pdf),
                'res_model': 'project.task',
                'res_id': self.event_id.id,
                'mimetype': 'application/pdf',
            })
            self.event_id.message_post(
                body=_("%(dept)s call-out certified by %(mgr)s.",
                       dept=self.department_label,
                       mgr=self.manager_partner_id.name or self.env.user.name),
                attachment_ids=[att.id],
                subtype_xmlid='mail.mt_note')
        except Exception:  # noqa: BLE001 - never block certification
            pass

    def _notify_coordinator_certified(self):
        self.ensure_one()
        try:
            user = self.event_id._after_action_user()
            summary = _("%(dept)s call-out certified: %(evt)s",
                        dept=self.department_label,
                        evt=self.event_id.name or 'Event')
            # Don't pile up duplicates: if this department already has an open
            # "certified" to-do for this event and user (from an earlier
            # certification / re-certification the coordinator hasn't cleared),
            # skip scheduling and emailing again.
            existing = self.env['mail.activity'].sudo().search([
                ('res_model', '=', 'project.task'),
                ('res_id', '=', self.event_id.id),
                ('user_id', '=', user.id),
                ('summary', '=', summary),
            ], limit=1)
            if existing:
                return
            self.event_id.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=summary,
                note=_("The %(dept)s manager certified their staffing for this "
                       "event. Review the updated estimate.",
                       dept=self.department_label),
                user_id=user.id)
            # Also email the coordinator directly.
            if user.email:
                base = self.env['ir.config_parameter'].sudo().get_param(
                    'web.base.url') or ''
                link = '%s/web#id=%s&model=project.task&view_type=form' % (
                    base, self.event_id.id)
                self.env['mail.mail'].sudo().create({
                    'subject': _('%(dept)s call-out certified - %(evt)s',
                                 dept=self.department_label,
                                 evt=self.event_id.name or 'Event'),
                    'body_html': _(
                        '<p>Hello %(name)s,</p><p>The %(dept)s manager '
                        '(%(mgr)s) certified their staffing for '
                        '<strong>%(evt)s</strong>: %(people)s people, '
                        '%(hours)g hours, $%(cost).2f labor'
                        '%(grat)s.</p><p><a href="%(link)s">Open the '
                        'event</a></p>',
                        name=user.name,
                        dept=self.department_label,
                        mgr=self.manager_partner_id.name or 'the manager',
                        evt=self.event_id.name or 'the event',
                        people=self.staff_count,
                        hours=self.hours_total,
                        cost=self.cost_total,
                        grat=(_(', $%.2f gratuity') % self.gratuity)
                        if self.gratuity else '',
                        link=link),
                    'email_to': user.email,
                }).send()
        except Exception:  # noqa: BLE001
            pass

    def _send_reminder(self):
        """Bump the manager: re-email the portal link for a call-out still
        awaiting certification. Best-effort; no-op once certified."""
        self.ensure_one()
        if self.state != 'sent':
            return False
        mgr = self.manager_partner_id
        base = self.env['ir.config_parameter'].sudo().get_param(
            'web.base.url') or ''
        link = '%s/my/callout/%s' % (base, self.id)
        if mgr and mgr.email:
            self.env['mail.mail'].sudo().create({
                'subject': _('Reminder: %(dept)s call-out needs your sign-off '
                             '- %(name)s',
                             dept=self.department_label,
                             name=self.event_id.name or 'Event'),
                'body_html': _(
                    '<p>Hello %(mgr)s,</p><p>Reminder: the %(dept)s call-out '
                    'for <strong>%(name)s</strong> still needs your review and '
                    'certification.</p><p><a href="%(link)s">Open the '
                    'call-out</a></p>',
                    mgr=mgr.name, dept=self.department_label,
                    name=self.event_id.name or 'the event', link=link),
                'email_to': mgr.email,
            }).send()
        self.event_id.message_post(
            body=_("Reminder sent to %(mgr)s for the %(dept)s call-out.",
                   mgr=(mgr.name or 'the manager'),
                   dept=self.department_label),
            subtype_xmlid='mail.mt_note')
        return True

    @api.model
    def _cron_remind_pending(self):
        """Daily bump for call-outs still awaiting certification, limited to
        events that have not yet happened."""
        today = fields.Date.context_today(self)
        for co in self.search([('state', '=', 'sent')]):
            evt_date = co.event_id.x_event_date
            if not evt_date or evt_date >= today:
                co._send_reminder()
        return True

    def action_open_portal_form(self):
        """Open the manager's portal call-sheet (coordinators may view it too)."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': '/my/callout/%s' % self.id,
            'target': 'new',
        }

    @api.model
    def _current_user_partner_ids(self):
        """Every partner id that represents the logged-in user, so we match a
        call-out's manager whether it was assigned to their portal contact,
        their employee work-contact, or a partner sharing their email."""
        user = self.env.user
        pids = set()
        if user.partner_id:
            pids.add(user.partner_id.id)
            if user.partner_id.commercial_partner_id:
                pids.add(user.partner_id.commercial_partner_id.id)
        emps = self.env['hr.employee'].sudo().search([
            '|', ('user_id', '=', user.id),
            ('work_email', '=ilike', user.email or '__none__')])
        for e in emps:
            if e.work_contact_id:
                pids.add(e.work_contact_id.id)
        if user.email:
            for p in self.env['res.partner'].sudo().search([
                    ('email', '=ilike', user.email)]):
                pids.add(p.id)
        return list(pids)

    @api.model
    def _current_user_callout_count(self):
        """Open (not-yet-certified) call-outs the current user should act on —
        for the /my card counter and its visibility."""
        if self.env.user.has_group('elksevent.group_event_coordinator'):
            return self.sudo().search_count([('state', '!=', 'certified')])
        return self.sudo().search_count([
            ('manager_partner_id', 'in', self._current_user_partner_ids())])

    @api.model
    def _current_user_has_callouts(self):
        """True if the current user is a coordinator or has ANY call-out (any
        state) assigned — drives the /my home card visibility."""
        if self.env.user.has_group('elksevent.group_event_coordinator'):
            return True
        return bool(self.sudo().search_count([
            ('manager_partner_id', 'in', self._current_user_partner_ids())]))

    @api.model
    def _get_or_create(self, event, department):
        """Return the single call-out for (event, department), creating and
        seeding it from the event on first use."""
        rec = self.search([
            ('event_id', '=', event.id),
            ('department', '=', department),
        ], limit=1)
        if not rec:
            rec = self.create({
                'event_id': event.id,
                'department': department,
                'manager_partner_id': event._callout_manager(department).id,
            })
            rec._seed_from_event()
        return rec


class EventCalloutLine(models.Model):
    _name = "elks.event.callout.line"
    _description = "Event Call-Out Staff Line"
    _order = "id"

    callout_id = fields.Many2one(
        'elks.event.callout', string="Call-Out", required=True,
        ondelete='cascade', index=True)
    employee_id = fields.Many2one(
        'hr.employee', string="Employee",
        help="Pick a lodge employee (links to payroll / attendance), or leave "
             "blank and type a name for outside help.")
    person_name = fields.Char(
        "Name", help="Free-text name when the person isn't a lodge employee.")
    role = fields.Char("Role / Shift")
    start_time = fields.Float(
        "Start", help="Shift start (24h, e.g. 15.5 = 3:30 PM).")
    end_time = fields.Float("End", help="Shift end (24h).")
    hours = fields.Float(
        "Hours",
        help="Shift length. Auto-filled from start/end when both are set, "
             "otherwise entered directly.")
    rate = fields.Monetary(
        "Rate / hr", currency_field='currency_id',
        help="Pay rate per hour you are paying this person.")
    bill_rate = fields.Monetary(
        "Billed / hr", currency_field='currency_id',
        compute='_compute_cost', store=True, readonly=True,
        help="Rate/hr charged to the event: your pay rate + the department's "
             "markup per hour from Lodge Settings.")
    raw_cost = fields.Monetary(
        "Labor Cost", currency_field='currency_id',
        compute='_compute_cost', store=True, readonly=True,
        help="Hours x your rate — what the lodge pays this person (the COGS).")
    cost = fields.Monetary(
        "Cost", currency_field='currency_id',
        compute='_compute_cost', store=True, readonly=True,
        help="Hours x billed rate — what the event is charged.")
    coverage = fields.Monetary(
        "Coverage", currency_field='currency_id',
        compute='_compute_cost', store=True, readonly=True,
        help="Billed minus paid (hours x (charge rate - your rate)) — the "
             "margin the lodge keeps toward the bartender pay pool.")
    gratuity_share = fields.Monetary(
        "Gratuity", currency_field='currency_id',
        help="This person's share of the department gratuity pool, as the "
             "manager wants it disbursed. Should add up to the pool total.")
    currency_id = fields.Many2one(related='callout_id.currency_id')

    @staticmethod
    def _round_up_5(value):
        """Round a dollar amount UP to the nearest $5."""
        import math
        return math.ceil((value or 0.0) / 5.0) * 5.0

    @api.depends('hours', 'rate', 'callout_id.department')
    def _compute_cost(self):
        settings = self.env['elks.lodge.settings'].sudo().search([], limit=1)
        # Default markup/hr used when a department has no specific markup.
        default_markup = (settings.x_labor_overhead_per_hour
                          if settings else 5.0) or 0.0
        markup_map = {
            'bar': (settings.x_callout_charge_bar if settings else 0.0),
            'kitchen': (settings.x_callout_charge_kitchen if settings else 0.0),
            'custodial': (
                settings.x_callout_charge_custodial if settings else 0.0),
        }
        for rec in self:
            raw = rec.rate or 0.0
            hrs = rec.hours or 0.0
            dept_markup = markup_map.get(rec.callout_id.department, 0.0) or 0.0
            markup = dept_markup if dept_markup > 0 else default_markup
            # Billed = the manager's pay rate + the markup per hour. The markup
            # x hours is the Coverage pool. No rounding.
            rec.bill_rate = (raw + markup) if raw else 0.0
            rec.raw_cost = hrs * raw
            rec.cost = hrs * rec.bill_rate
            rec.coverage = rec.cost - rec.raw_cost
    display_name = fields.Char(compute='_compute_display_name')
    start_str = fields.Char(compute='_compute_time_str')
    end_str = fields.Char(compute='_compute_time_str')

    @api.depends('employee_id', 'person_name')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = (
                rec.employee_id.name or rec.person_name or _("(unnamed)"))

    @api.depends('start_time', 'end_time')
    def _compute_time_str(self):
        """HH:MM strings for the <input type='time'> fields on the portal."""
        for rec in self:
            rec.start_str = self._float_to_hhmm(rec.start_time)
            rec.end_str = self._float_to_hhmm(rec.end_time)

    @staticmethod
    def _float_to_hhmm(value):
        if not value:
            return ''
        hh = int(value)
        mm = int(round((value - hh) * 60))
        if mm == 60:
            hh += 1
            mm = 0
        return '%02d:%02d' % (hh % 24, mm)
