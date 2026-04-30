# -*- coding: utf-8 -*-
{
    'name': 'Elks Event Bookings',
    'version': '19.0.2.1.0',
    'category': 'Services/Project',
    'summary': 'Facility event booking, board approval, room billing, and P&L reporting',
    'description': """
Extends Odoo's Project module to manage facility event bookings for the lodge.

Key features
============
- Public website form for event requests
- Kanban pipeline: New → Contacted → Submitted to Board → Booked → After Action → Completed / Cancelled
- Board approval workflow with secretary notification
- Room / venue selection from rentable lodge locations
- Event cost tracking (setup, event, cleanup hours, supplies, bar services, coordinator fee)
- Coordinator fee — fixed rate or percentage of room income (configurable)
- Single-line or itemized-by-room customer invoicing
- Budget control — costs cannot exceed billed amount without authorised override
- Per-event and date-range P&L reports with Elks lodge header
- Auto-creation of Elk Year parent project (April – March)
    """,
    'author': 'Lewiston Clarkston Elks Lodge #896',
    'website': 'https://lcvalleyelks.org',
    'license': 'LGPL-3',
    'depends': [
        'project',
        'mail',
        'website',
        'account',
        'purchase',
        'elksfrs',
        'elksmaintenance',
        'elkspurchase',
    ],
    'data': [
        'security/event_security.xml',
        'security/ir.model.access.csv',
        'data/event_stages.xml',
        'data/event_cron.xml',
        'views/event_task_views.xml',
        'views/event_room_views.xml',
        'views/event_menus.xml',
        'views/website_event_form.xml',
        'report/event_pl_report.xml',
    ],
    'assets': {},
    'installable': True,
    'application': True,
    'auto_install': False,
}
