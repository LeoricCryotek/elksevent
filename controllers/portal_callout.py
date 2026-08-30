# -*- coding: utf-8 -*-
"""Website-portal access for department managers to review + certify their
event call-outs.

HUMAN
-----
A Bar / Kitchen / Custodial manager logs into the lodge website. On their
account page (/my) a "Department Call-Outs" card lists the events they've been
asked to staff. Opening one shows the estimated staff counts/hours, which they
can adjust, add notes to, and CERTIFY. Certifying locks the numbers onto the
event and files the call-out for the coordinator.

AI
--
- Extends CustomerPortal: home counter, list page, detail page + POST.
- Ownership is enforced by matching manager_partner_id to the logged-in
  partner; the actual writes go through sudo() after that check.
"""
import pytz

from odoo import _, fields, http
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal

# Fields we accept per department on the portal POST (so saving one
# department's form never clobbers another's stored values).
_DEPT_POST_FIELDS = {
    'bar': ('num_bars', 'primary_count', 'primary_hours', 'drink_requests'),
    'kitchen': ('primary_count', 'primary_hours',
                'secondary_count', 'secondary_hours'),
    'custodial': ('primary_count', 'primary_hours', 'ready_datetime'),
}


class CalloutPortal(CustomerPortal):

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'callout_count' in counters:
            partner = request.env.user.partner_id
            # sudo: the count is hard-filtered to the caller's own partner, and
            # running non-sudo would raise for internal users without an ACL row.
            values['callout_count'] = request.env['elks.event.callout'].sudo(
            ).search_count(
                [('manager_partner_id', '=', partner.id)]) if partner else 0
        return values

    def _user_tz(self):
        return pytz.timezone(request.env.user.tz or 'UTC')

    def _callout_owned(self, callout_id):
        """Return the call-out if the current user may act on it, else None."""
        co = request.env['elks.event.callout'].sudo().browse(callout_id)
        if not co.exists():
            return None
        user = request.env.user
        if (co.manager_partner_id.id == user.partner_id.id
                or user.has_group('elksevent.group_event_coordinator')):
            return co
        return None

    @http.route(['/my/callouts', '/my/callouts/page/<int:page>'],
                type='http', auth='user', website=True)
    def portal_my_callouts(self, page=1, **kw):
        partner = request.env.user.partner_id
        callouts = request.env['elks.event.callout'].sudo().search(
            [('manager_partner_id', '=', partner.id)], order='create_date desc')
        return request.render('elksevent.portal_my_callouts', {
            'callouts': callouts,
            'page_name': 'callout',
            'default_url': '/my/callouts',
        })

    @http.route(['/my/callout/<int:callout_id>'],
                type='http', auth='user', website=True)
    def portal_callout_page(self, callout_id, **kw):
        co = self._callout_owned(callout_id)
        if not co:
            return request.redirect('/my')
        # Localize the "building ready by" datetime for the datetime-local input.
        ready_local = ''
        if co.ready_datetime:
            ready_local = fields.Datetime.context_timestamp(
                co, co.ready_datetime).strftime('%Y-%m-%dT%H:%M')
        employees = request.env['hr.employee'].sudo().search(
            [], order='name')
        return request.render('elksevent.portal_callout_page', {
            'callout': co,
            'ready_local': ready_local,
            'employees': employees,
            'page_name': 'callout',
            'saved': kw.get('saved'),
            'error': kw.get('error'),
        })

    @http.route(['/my/callout/<int:callout_id>/submit'],
                type='http', auth='user', methods=['POST'], website=True)
    def portal_callout_submit(self, callout_id, **post):
        co = self._callout_owned(callout_id)
        if not co:
            return request.redirect('/my')
        if co.state == 'certified':
            return request.redirect('/my/callout/%s' % co.id)

        def _int(v):
            try:
                return int(float(v))
            except (TypeError, ValueError):
                return 0

        def _float(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0

        # The portal now captures actual staffing in the roster below; the
        # requested counts stay on the event (read-only here). So only the
        # manager notes and (bar/kitchen) gratuity are written from the header.
        vals = {'manager_notes': post.get('manager_notes') or ''}
        if co.department in ('bar', 'kitchen'):
            vals['gratuity'] = _float(post.get('gratuity'))
        co.sudo().write(vals)

        # Rebuild the named staff roster from the submitted rows (stateless:
        # whatever is in the rows becomes the roster). The employee <select>
        # posts for every rendered row, so iterate while it's present. A row is
        # kept only if it names someone (employee or free-text) or has a value.
        def _hhmm(v):
            """Parse an <input type='time'> value ('HH:MM') to float hours."""
            if not v or ':' not in v:
                return 0.0
            try:
                hh, mm = v.split(':')[:2]
                return int(hh) + int(mm) / 60.0
            except (TypeError, ValueError):
                return 0.0

        line_cmds = [(5, 0, 0)]
        i = 0
        while ('emp_%d' % i) in post:
            emp_raw = (post.get('emp_%d' % i) or '').strip()
            pname = (post.get('pname_%d' % i) or '').strip()
            role = (post.get('role_%d' % i) or '').strip()
            start = _hhmm(post.get('start_%d' % i))
            end = _hhmm(post.get('end_%d' % i))
            rate = _float(post.get('rate_%d' % i))
            # Hours: computed from start/end when both set (span midnight ->
            # +24), else the directly-typed hours value.
            if start and end:
                hours = end - start
                if hours < 0:
                    hours += 24.0
            else:
                hours = _float(post.get('hours_%d' % i))
            emp_id = int(emp_raw) if emp_raw.isdigit() else False
            if emp_id or pname or hours or rate or start or end:
                line_cmds.append((0, 0, {
                    'employee_id': emp_id or False,
                    'person_name': pname,
                    'role': role,
                    'start_time': start,
                    'end_time': end,
                    'hours': hours,
                    'rate': rate,
                }))
            i += 1
        co.sudo().write({'line_ids': line_cmds})

        if post.get('certify'):
            co.sudo().action_certify()
        return request.redirect('/my/callout/%s?saved=1' % co.id)

    @http.route(['/my/callout/<int:callout_id>/reopen'],
                type='http', auth='user', methods=['POST'], website=True)
    def portal_callout_reopen(self, callout_id, **post):
        """Let the manager reopen a certified call-out to make edits."""
        co = self._callout_owned(callout_id)
        if not co:
            return request.redirect('/my')
        if co.state == 'certified':
            co.sudo().write({'state': 'sent'})
            # Drop the roster labor line while it's back in draft.
            co.event_id.sudo()._sync_labor_cost_lines()
            co.event_id.message_post(
                body=_("%(dept)s call-out reopened for editing by %(who)s.",
                       dept=co.department_label,
                       who=request.env.user.name),
                subtype_xmlid='mail.mt_note')
        return request.redirect('/my/callout/%s' % co.id)
