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
from datetime import date

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

    def _is_event_coordinator(self):
        return request.env.user.has_group('elksevent.group_event_coordinator')

    def _callout_domain(self):
        """What this user may see: coordinators see every call-out; everyone
        else sees only the ones they're the manager for (matched across their
        portal contact / employee work-contact / same-email partners)."""
        if self._is_event_coordinator():
            return []
        pids = request.env['elks.event.callout'].sudo(
        )._current_user_partner_ids()
        return [('manager_partner_id', 'in', pids)]

    def _prepare_home_portal_values(self, counters):
        # NOTE: only counter keys may go in this dict — /my/counters returns it
        # verbatim to the JS, which then sets textContent on a matching DOM
        # element per key. A non-counter key (with no element) crashes it. The
        # card's visibility is decided in the template instead.
        values = super()._prepare_home_portal_values(counters)
        if 'callout_count' in counters:
            # Count what still needs finishing (not yet certified) so the card
            # nudges area managers / coordinators toward open work.
            values['callout_count'] = request.env['elks.event.callout'].sudo(
            ).search_count(
                self._callout_domain() + [('state', '!=', 'certified')])
        return values

    def _user_tz(self):
        return pytz.timezone(request.env.user.tz or 'UTC')

    def _callout_owned(self, callout_id):
        """Return the call-out if the current user may act on it, else None."""
        co = request.env['elks.event.callout'].sudo().browse(callout_id)
        if not co.exists():
            return None
        user = request.env.user
        pids = request.env['elks.event.callout'].sudo(
        )._current_user_partner_ids()
        if (co.manager_partner_id.id in pids
                or user.has_group('elksevent.group_event_coordinator')):
            return co
        return None

    @http.route(['/my/callouts', '/my/callouts/page/<int:page>'],
                type='http', auth='user', website=True)
    def portal_my_callouts(self, page=1, sortby=None, filterby=None,
                           search=None, search_in='event', **kw):
        today = fields.Date.context_today(self)
        Callout = request.env['elks.event.callout'].sudo()

        # Sort options (event-level) offered in the dropdown + column headers.
        searchbar_sortings = {
            'date': {'label': _('Event Date (soonest)'), 'order': 'asc'},
            'date_desc': {'label': _('Event Date (furthest)'), 'order': 'desc'},
            'name': {'label': _('Event Name'), 'order': 'name'},
            'status': {'label': _('Status'), 'order': 'status'},
        }
        if sortby not in searchbar_sortings:
            sortby = 'date'

        # Filters — past events hidden by default (Upcoming).
        upcoming_dom = ['|', ('event_id.x_event_date', '>=', today),
                        ('event_id.x_event_date', '=', False)]
        searchbar_filters = {
            'upcoming': {'label': _('Upcoming'), 'domain': upcoming_dom},
            'needs': {'label': _('Needs staffing'),
                      'domain': upcoming_dom + [('state', '=', 'sent')]},
            'certified': {'label': _('Certified'),
                          'domain': upcoming_dom + [('state', '=', 'certified')]},
            'all': {'label': _('All (incl. past)'), 'domain': []},
        }
        if filterby not in searchbar_filters:
            filterby = 'upcoming'

        # Search box: which field to match.
        searchbar_inputs = {
            'event': {'input': 'event', 'label': _('Search in Event')},
            'manager': {'input': 'manager', 'label': _('Search in Manager')},
            'department': {'input': 'department',
                           'label': _('Search in Department')},
        }
        if search_in not in searchbar_inputs:
            search_in = 'event'

        domain = self._callout_domain() + searchbar_filters[filterby]['domain']
        if search:
            if search_in == 'manager':
                domain += [('manager_partner_id.name', 'ilike', search)]
            elif search_in == 'department':
                domain += [('department', 'ilike', search)]
            else:
                domain += [('event_id.name', 'ilike', search)]

        callouts = Callout.search(domain)

        # Group by event.
        by_event = {}
        for co in callouts:
            by_event.setdefault(co.event_id.id, {
                'event': co.event_id, 'callouts': Callout})
            by_event[co.event_id.id]['callouts'] |= co
        groups = list(by_event.values())

        # Order the event groups per the chosen sort.
        state_rank = {'sent': 0, 'draft': 1, 'certified': 2}
        if sortby == 'name':
            groups.sort(key=lambda g: (g['event'].name or '').lower())
        elif sortby == 'status':
            groups.sort(key=lambda g: min(
                (state_rank.get(c.state, 9) for c in g['callouts']), default=9))
        elif sortby == 'date_desc':
            groups.sort(key=lambda g: g['event'].x_event_date or date.min,
                        reverse=True)
        else:  # 'date' — soonest first, undated last
            groups.sort(key=lambda g: g['event'].x_event_date or date.max)

        return request.render('elksevent.portal_my_callouts', {
            'groups': groups,
            'is_coordinator': self._is_event_coordinator(),
            'page_name': 'callout',
            'default_url': '/my/callouts',
            'sortby': sortby,
            'searchbar_sortings': searchbar_sortings,
            'filterby': filterby,
            'searchbar_filters': searchbar_filters,
            'search': search or '',
            'search_in': search_in,
            'searchbar_inputs': searchbar_inputs,
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
        # Exclude anyone already committed on ANOTHER call-out for this same
        # event, so a person can't be double-booked across departments. People
        # already on THIS call-out stay in the list (they need to render).
        taken = request.env['elks.event.callout.line'].sudo().search([
            ('callout_id.event_id', '=', co.event_id.id),
            ('callout_id', '!=', co.id),
            ('employee_id', '!=', False),
        ]).mapped('employee_id')
        employees = request.env['hr.employee'].sudo().search(
            [('id', 'not in', taken.ids)], order='name')
        return request.render('elksevent.portal_callout_page', {
            'callout': co,
            'ready_local': ready_local,
            'employees': employees,
            'cal': self._mini_calendar(co.event_id.x_event_date),
            'page_name': 'callout',
            'saved': kw.get('saved'),
            'error': kw.get('error'),
        })

    def _mini_calendar(self, day):
        """Data for a small month calendar on the form, highlighting the event
        day. Sunday-first weeks; 0 marks padding days outside the month."""
        if not day:
            return None
        import calendar as _cal
        return {
            'title': day.strftime('%B %Y'),
            'weeks': _cal.Calendar(firstweekday=6).monthdayscalendar(
                day.year, day.month),
            'day': day.day,
        }

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
            grat = _float(post.get('grat_%d' % i))
            emp_id = int(emp_raw) if emp_raw.isdigit() else False
            if emp_id or pname or hours or rate or start or end or grat:
                line_cmds.append((0, 0, {
                    'employee_id': emp_id or False,
                    'person_name': pname,
                    'role': role,
                    'start_time': start,
                    'end_time': end,
                    'hours': hours,
                    'rate': rate,
                    'gratuity_share': grat,
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
