# -*- coding: utf-8 -*-
{
    'name': 'Elks Event Bookings',
    'version': '19.0.3.3.0',
    'category': 'Services/Project',
    'summary': 'Facility event booking, board approval, room billing, and P&L reporting',
    'description': """
Extends Odoo's Project module to manage facility event bookings for the lodge.

Key features
============
- Public website form for event requests
- Kanban pipeline: New → Contacted → Submitted to Board → Booked → After Action → Completed / Cancelled
- Board + membership-floor approval workflow (Event Officer group) with secretary notification
- Room / venue selection from rentable lodge locations
- Staff-only itemized quote engine (sale.order); customer sees a single event rental price
- Labor hours tracked from the timeclock (hr.attendance) as an internal P&L cost
- Coordinator fee — fixed rate or percentage of room income (configurable)
- Two-invoice billing: reservation deposit + final balance (Clover-ready)
- Member and Celebration-of-Life pricing rules
- Approved events auto-publish to the lodge calendar
- Assessor / UBI reporting and AP GL breakout by FRS account
- Per-event and date-range P&L reports with Elks lodge header
- All events file into one global "Events" project
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
        'sale_management',
        'hr_attendance',
        'elksfrs',
        'elksmaintenance',
        'elkspurchase',
    ],
    'data': [
        'security/event_security.xml',
        'security/ir.model.access.csv',
        'data/event_stages.xml',
        'data/event_cron.xml',
        'data/event_checklist_data.xml',
        'data/event_mail_templates.xml',
        'data/website_form_data.xml',
        'wizard/event_approval_wizard_views.xml',
        'views/event_checklist_views.xml',
        'views/event_product_views.xml',
        'views/event_task_views.xml',
        'views/event_room_views.xml',
        'views/hr_attendance_views.xml',
        'report/event_pl_report.xml',
        'report/event_assessor_report.xml',
        'report/event_ap_breakout_report.xml',
        'report/event_contract_report.xml',
        'views/event_menus.xml',
        'views/website_event_form.xml',
        'views/event_form_snippet.xml',
    ],
    'assets': {},
    'installable': True,
    'application': True,
    'auto_install': False,
}
