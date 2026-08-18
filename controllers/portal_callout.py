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

from odoo import fields, http
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
        return request.render('elksevent.portal_callout_page', {
            'callout': co,
            'ready_local': ready_local,
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

        # Only accept the fields this department actually shows, so saving
        # never zeroes another department's stored values. Notes always apply.
        allowed = _DEPT_POST_FIELDS.get(co.department, ())
        vals = {'manager_notes': post.get('manager_notes') or ''}
        if 'num_bars' in allowed:
            vals['num_bars'] = _int(post.get('num_bars'))
        if 'primary_count' in allowed:
            vals['primary_count'] = _int(post.get('primary_count'))
        if 'primary_hours' in allowed:
            vals['primary_hours'] = _float(post.get('primary_hours'))
        if 'secondary_count' in allowed:
            vals['secondary_count'] = _int(post.get('secondary_count'))
        if 'secondary_hours' in allowed:
            vals['secondary_hours'] = _float(post.get('secondary_hours'))
        if 'drink_requests' in allowed:
            vals['drink_requests'] = post.get('drink_requests') or ''
        if 'ready_datetime' in allowed:
            raw_ready = post.get('ready_datetime')
            if raw_ready:
                # datetime-local ("2026-08-14T17:00") is LOCAL wall-clock;
                # convert to naive UTC for storage.
                naive = raw_ready.replace('T', ' ')
                if len(naive) == 16:
                    naive += ':00'
                local = self._user_tz().localize(
                    fields.Datetime.to_datetime(naive))
                vals['ready_datetime'] = local.astimezone(
                    pytz.utc).replace(tzinfo=None)
            else:
                vals['ready_datetime'] = False
        co.sudo().write(vals)
        if post.get('certify'):
            co.sudo().action_certify()
        return request.redirect('/my/callout/%s?saved=1' % co.id)
