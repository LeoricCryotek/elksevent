# -*- coding: utf-8 -*-
"""Public website controller for event booking requests.

HUMAN
-----
This is the public "Request an Event" page. A visitor fills out their
details, the checklist questions, and any promo links, and submits. Behind
the scenes we find or create their contact, open an event in the current
Elk-Year project, log exactly what they asked for to the event's history,
start a draft quote and a tentative calendar hold, and email them a receipt.

AI
--
- Routes: GET /event-request (form), POST /event-request/submit (csrf=True,
  auth='public'). All record writes go through sudo().
- Customer matched by email (=ilike) else created with customer_rank=1.
- Checklist answers parsed into x_* fields; helpers _int / _us_them validate.
- Post-create best-effort (never blocks submission): _create_event_quote,
  _ensure_placeholder_calendar, _send_event_mail(request_received).
- _get_event_project mirrors project_task's Elk-Year logic (April–March).
"""
from datetime import datetime

from odoo import http
from odoo.http import request


class WebsiteEventRequest(http.Controller):

    @http.route(
        '/event-request', type='http', auth='public',
        website=True, sitemap=True,
    )
    def event_request_form(self, **kw):
        """Display the public event request form."""
        venues = request.env['maintenance.location'].sudo().search([
            ('x_is_rentable', '=', True),
            ('active', '=', True),
        ], order='sequence, name')
        settings = request.env['elks.lodge.settings'].sudo().search([], limit=1)

        def _price(prod):
            return prod.list_price if prod else 0.0

        pricing = {
            'per_plate': (settings.x_per_plate_charge if settings else 0) or 0,
            'gpb': (settings.x_guests_per_bartender if settings else 0) or 100,
            'bartender': _price(settings.x_bartender_product_id) if settings else 0,
            'bar': _price(settings.x_bar_product_id) if settings else 0,
            'coord_type': (settings.x_coordinator_fee_type if settings else 'percentage') or 'percentage',
            'coord_pct': (settings.x_coordinator_percentage if settings else 0) or 0,
            'coord_fixed': (settings.x_coordinator_fixed_rate if settings else 0) or 0,
            'deposit_pct': (settings.x_deposit_pct if settings else 50) or 50,
            'member_pct': (settings.x_member_discount_pct if settings else 0) or 0,
            'event_labor': _price(settings.x_event_labor_product_id) if settings else 0,
            'cleaning': _price(settings.x_cleaning_product_id) if settings else 0,
            'event_hours': (settings.x_default_event_hours if settings else 0) or 0,
            'cleaning_hours': (settings.x_default_cleaning_hours if settings else 0) or 0,
        }
        # Dates already reserved (deposit paid) or approved — shown as
        # unavailable on the booking form's date picker.
        booked = request.env['project.task'].sudo().search([
            ('x_is_event', '=', True),
            ('x_event_date', '!=', False),
            ('x_approval_state', '!=', 'rejected'),
            '|', ('x_approval_state', '=', 'approved'),
                 ('x_deposit_received', '=', True),
        ])
        blocked_dates = sorted(set(
            d.strftime('%Y-%m-%d') for d in booked.mapped('x_event_date')))

        return request.render('elksevent.website_event_request_form', {
            'venues': venues,
            'floor_plan': settings.x_event_floor_plan if settings else False,
            'max_bars': (settings.x_max_bars if settings else 0) or 4,
            'pricing': pricing,
            'blocked_dates': blocked_dates,
        })

    @http.route(
        '/event-request/submit', type='http', auth='public',
        website=True, csrf=True, methods=['POST'],
    )
    def event_request_submit(self, **post):
        """Process the event request form submission."""
        event_project = self._get_event_project()
        new_stage = request.env.ref(
            'elksevent.stage_new', raise_if_not_found=False,
        )
        if not event_project:
            return request.render('elksevent.website_event_request_error', {
                'error': 'Event booking system is not configured.',
            })

        # Parse the event date
        event_date = False
        if post.get('event_date'):
            try:
                event_date = datetime.strptime(
                    post['event_date'], '%Y-%m-%d'
                ).date()
            except (ValueError, TypeError):
                pass

        start_time = self._parse_time(post.get('start_time', ''))
        end_time = self._parse_time(post.get('end_time', ''))

        guest_count = 0
        try:
            guest_count = int(post.get('guest_count', 0))
        except (ValueError, TypeError):
            pass

        event_type = post.get('event_type', '')
        valid_types = [
            'wedding', 'birthday', 'anniversary', 'corporate',
            'fundraiser', 'memorial', 'meeting', 'holiday', 'other',
        ]
        if event_type not in valid_types:
            event_type = 'other'

        partner = self._find_or_create_partner(post)

        # Cap the number of bars to the lodge's available bar locations.
        settings = request.env['elks.lodge.settings'].sudo().search([], limit=1)
        max_bars = (settings.x_max_bars if settings else 0) or 4
        num_bars = min(self._int(post.get('num_bars')), max_bars)

        # Elk membership: when not a member (field hidden on the form), store
        # nine zeros as a sentinel rather than leaving it blank. For a
        # Celebration of Life, the family enters the (deceased) member's number
        # in a separate field — use that regardless of member status.
        is_member = post.get('is_member') == 'yes'
        member_number = (post.get('member_number') or '').strip()
        col_is_elk = post.get('col_is_elk') == 'yes'
        col_number = (post.get('col_member_number') or '').strip()
        # For a Celebration of Life honoring an Elk, the honoree's number gives
        # the family the member rate / free room.
        if col_is_elk and col_number:
            member_number = col_number
        elif not is_member or not member_number:
            member_number = '000000000'

        # Linen color: a base color or a free-text "Other" request.
        linen_color = (post.get('linen_color') or '').strip()
        if linen_color == 'Other':
            linen_color = (post.get('linen_color_other') or '').strip()

        vals = {
            'name': post.get('event_name', 'Event Request'),
            'project_id': event_project.id,
            'stage_id': new_stage.id if new_stage else False,
            'partner_id': partner.id if partner else False,
            'x_event_type': event_type,
            'x_event_date': event_date,
            'x_event_start_time': start_time,
            'x_event_end_time': end_time,
            'x_guest_count': guest_count,
            'x_customer_name': post.get('customer_name', ''),
            'x_customer_phone': post.get('customer_phone', ''),
            'x_customer_email': post.get('customer_email', ''),
            'x_is_member': is_member,
            'x_member_number': member_number,
            # Celebration-of-Life honoree (Elk) details
            'x_col_is_elk': col_is_elk,
            'x_col_member_number': col_number,
            'x_col_home_lodge': post.get('col_home_lodge', ''),
            # K&K rental-insurance prohibited-activities acknowledgment
            'x_prohibited_ack': bool(post.get('prohibited_ack')),
            'x_event_description': post.get('event_description', ''),
            'x_special_requests': post.get('special_requests', ''),
            # Promotional links
            'x_facebook_event_url': post.get('facebook_url', ''),
            'x_related_url': post.get('related_url', ''),
            # Checklist answers
            'x_need_tables': self._int(post.get('need_tables')),
            'x_need_chairs': self._int(post.get('need_chairs')),
            'x_num_bars': num_bars,
            'x_food_request': bool(post.get('food_request')),
            'x_catering_details': post.get('catering_details', ''),
            'x_need_podium': bool(post.get('need_podium')),
            'x_need_sound': bool(post.get('need_sound')),
            'x_need_microphone': bool(post.get('need_microphone')),
            'x_need_linen': bool(post.get('need_linen')),
            'x_need_insurance': True,
            'x_insurance_by': (
                post.get('insurance_by')
                if post.get('insurance_by') in ('renter', 'lodge')
                else 'lodge'),
            # Garbage is always handled by the lodge.
            'x_need_garbage': True,
            'x_need_beer_tub': bool(post.get('need_beer_tub')),
            'x_has_decorations': bool(post.get('has_decorations')),
            'x_decoration_notes': post.get('decoration_notes', ''),
            'x_food_by_us': bool(post.get('food_by_us')),
            'x_food_menu': post.get('food_menu', ''),
            'x_linen_qty': self._int(post.get('linen_qty')),
            'x_linen_color': linen_color,
            'x_setup_by': self._us_them(post.get('setup_by')),
            'x_takedown_by': self._us_them(post.get('takedown_by')),
            'x_decor_takedown_by': self._us_them(post.get('decor_takedown_by')),
            'x_extras': post.get('extras', ''),
        }

        # Selected rooms -> the Requested Rooms m2m; the model reconciles it
        # into booking lines on create (single source of truth for rooms).
        venue_ids = []
        for vid in request.httprequest.form.getlist('venue_ids'):
            try:
                venue = request.env['maintenance.location'].sudo().browse(
                    int(vid))
            except (ValueError, TypeError):
                continue
            if venue.exists() and venue.x_is_rentable:
                venue_ids.append(venue.id)
        if venue_ids:
            vals['x_requested_room_ids'] = [(6, 0, venue_ids)]

        task = request.env['project.task'].sudo().create(vals)

        # Log the submitted answers to the chatter for the audit trail
        task.sudo().message_post(
            body=(
                "<b>Public event request submitted.</b><br/>"
                "Customer: %s (%s, %s)<br/>"
                "Guests: %s | Venue requested: %s"
            ) % (
                post.get('customer_name', ''),
                post.get('customer_email', ''),
                post.get('customer_phone', ''),
                guest_count,
                post.get('venue_id', '—'),
            ),
            subtype_xmlid='mail.mt_note',
        )

        # Best-effort: start a draft quote, a tentative calendar entry,
        # and send the confirmation email. Never break the submission.
        try:
            task.sudo()._create_event_quote()
        except Exception:  # noqa: BLE001
            pass
        try:
            task.sudo()._ensure_placeholder_calendar()
        except Exception:  # noqa: BLE001
            pass
        try:
            task.sudo()._send_event_mail('elksevent.tmpl_event_request_received')
        except Exception:  # noqa: BLE001
            pass

        return request.render('elksevent.website_event_request_thanks', {
            'task': task,
        })

    @staticmethod
    def _int(val):
        try:
            return int(val)
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _us_them(val):
        """Validate an 'us'/'them' selection from the form."""
        return val if val in ('us', 'them') else False

    def _find_or_create_partner(self, post):
        """Find a partner by email, or create one from the form fields."""
        Partner = request.env['res.partner'].sudo()
        email = (post.get('customer_email') or '').strip()
        name = (post.get('customer_name') or '').strip()
        phone = (post.get('customer_phone') or '').strip()
        partner = Partner.browse()
        if email:
            partner = Partner.search([('email', '=ilike', email)], limit=1)
        if not partner and (name or email):
            partner = Partner.create({
                'name': name or email or 'Event Customer',
                'email': email or False,
                'phone': phone or False,
                'customer_rank': 1,
            })
        return partner

    def _get_event_project(self):
        """All new requests go to one global Events project.

        Use the 'Default Events Project' from Lodge Settings if set, otherwise
        the base 'Events' project. (Year folders are not used for routing.)
        """
        settings = request.env['elks.lodge.settings'].sudo().search(
            [], limit=1)
        if settings and settings.x_event_project_id:
            return settings.x_event_project_id
        return request.env.ref(
            'elksevent.project_event_bookings', raise_if_not_found=False)

    @staticmethod
    def _parse_time(time_str):
        """Convert 'HH:MM' to Odoo float time (e.g. '14:30' → 14.5)."""
        if not time_str:
            return 0.0
        try:
            parts = time_str.split(':')
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            return h + m / 60.0
        except (ValueError, IndexError):
            return 0.0
