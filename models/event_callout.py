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

    certified_by = fields.Many2one('res.users', string="Certified By",
                                   copy=False)
    certified_on = fields.Datetime("Certified On", copy=False)

    # Convenience mirrors for the portal templates
    event_name = fields.Char(related='event_id.name', string="Event")
    event_date = fields.Date(related='event_id.x_event_date', string="Date")
    department_label = fields.Char(
        compute='_compute_department_label', string="Department Name")
    currency_id = fields.Many2one(related='event_id.x_currency_id')

    @api.depends('department')
    def _compute_department_label(self):
        labels = dict(CALLOUT_DEPARTMENTS)
        for rec in self:
            rec.department_label = labels.get(rec.department, '')

    # ── Sync helpers ───────────────────────────────────────────────────
    def _seed_from_event(self):
        """Copy the event's current estimate into this call-out."""
        for rec in self:
            evt = rec.event_id
            fmap = FIELD_MAP.get(rec.department, {})
            vals = {co_f: evt[task_f] for co_f, task_f in fmap.items()}
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
            rec.sudo()._push_to_event()
            rec.sudo()._attach_certified_pdf()
            rec.sudo()._notify_coordinator_certified()
        return True

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
            self.event_id.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=_("%(dept)s call-out certified: %(evt)s",
                          dept=self.department_label,
                          evt=self.event_id.name or 'Event'),
                note=_("The %(dept)s manager certified their staffing for this "
                       "event. Review the updated estimate.",
                       dept=self.department_label),
                user_id=user.id)
        except Exception:  # noqa: BLE001
            pass

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
