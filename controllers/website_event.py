# -*- coding: utf-8 -*-
"""Public website controller for event booking requests.

Renders a form at /event-request that anyone can fill out.
On submit, a new project.task is created in the current Elk Year
(or default) Event Bookings project at the "New" stage.
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
        return request.render('elksevent.website_event_request_form', {
            'venues': venues,
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

        vals = {
            'name': post.get('event_name', 'Event Request'),
            'project_id': event_project.id,
            'stage_id': new_stage.id if new_stage else False,
            'x_event_type': event_type,
            'x_event_date': event_date,
            'x_event_start_time': start_time,
            'x_event_end_time': end_time,
            'x_guest_count': guest_count,
            'x_customer_name': post.get('customer_name', ''),
            'x_customer_phone': post.get('customer_phone', ''),
            'x_customer_email': post.get('customer_email', ''),
            'x_event_description': post.get('event_description', ''),
            'x_special_requests': post.get('special_requests', ''),
        }

        # Add requested venue as a room booking if provided
        venue_id = post.get('venue_id')
        if venue_id:
            try:
                venue_id = int(venue_id)
                venue = request.env['maintenance.location'].sudo().browse(
                    venue_id
                )
                if venue.exists() and venue.x_is_rentable:
                    vals['x_room_booking_ids'] = [(0, 0, {
                        'room_id': venue.id,
                        'rate': venue.x_room_rate,
                        'cleaning_fee': venue.x_cleaning_fee,
                        'service_fee': venue.x_service_fee,
                    })]
            except (ValueError, TypeError):
                pass

        task = request.env['project.task'].sudo().create(vals)

        return request.render('elksevent.website_event_request_thanks', {
            'task': task,
        })

    def _get_event_project(self):
        """Get the current Elk Year project, falling back to base."""
        from odoo import fields as odoo_fields
        today = odoo_fields.Date.today()
        if today.month < 4:
            year_label = f"{today.year - 1}-{today.year}"
        else:
            year_label = f"{today.year}-{today.year + 1}"
        project_name = f"Events {year_label}"
        project = request.env['project.project'].sudo().search([
            ('name', '=', project_name),
        ], limit=1)
        if not project:
            project = request.env.ref(
                'elksevent.project_event_bookings',
                raise_if_not_found=False,
            )
        return project

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
