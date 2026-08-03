# -*- coding: utf-8 -*-
{
    'name': 'Elks Event Bookings',
    'version': '19.0.9.0.0',
    'category': 'Services/Project',
    'summary': 'Facility event booking, board approval, room billing, and P&L reporting',
    'description': """
Extends Odoo's Project module to manage facility event bookings for the lodge,
from the public request form through board/floor approval, quoting, billing,
labor reconciliation, and the P&L.

Key features
============
- Public website form for event requests (no website menu needed), plus an
  editable public Rental Terms & Conditions page linked on every invoice.
- Kanban pipeline: New → Contacted → Submitted to Board → Booked → After Action
  → Completed / Cancelled.
- Board + membership-floor approval workflow (Event Officer group) with
  secretary notification and a floor-vote wizard.
- Room / venue selection from rentable lodge locations; each room carries its
  rate, cleaning and service fees, capacity, and an FRS income product.
- Single "Event Rental" customer quote (rooms + Event Costs + coordinator fee),
  built by the quote engine; tax applies to taxable goods only, with a
  non-taxable services line and a "taxes on taxable items only" note.
- Configurable Event Cost types grouped into P&L categories (Catering / Bar /
  Event Services / Cleaning), with a per-line COGS column that tallies to the
  Financials P&L by category.
- Labor estimated from planning inputs (setup / event / teardown, bartenders x
  hours, cleaning) per role, reconciled to actual timeclock hours via an
  Add-Hours picker and True-up; actual labor cost rolls into the P&L.
- Coordinator fee — fixed rate or % of room income, auto-suggested but editable
  and only changed on demand.
- Two-invoice billing: reservation deposit + final balance (Clover-ready). The
  final invoice credits the paid deposit; both print the deposit as a receipt.
- Member and Celebration-of-Life pricing rules.
- Approved events auto-publish to the lodge calendar (with the calendar
  description) for the Elks Calendar / newsletter.
- Assessor / UBI reporting and AP GL breakout by FRS account; per-event and
  date-range P&L reports with the Elks lodge header.
- All events file into one global "Events" project.
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
        'data/event_cost_types.xml',
        'data/event_mail_templates.xml',
        'data/website_form_data.xml',
        'wizard/event_approval_wizard_views.xml',
        'wizard/event_add_hours_wizard_views.xml',
        'report/event_paperformat.xml',
        'report/event_pl_report.xml',
        'report/event_assessor_report.xml',
        'report/event_ap_breakout_report.xml',
        'report/event_contract_report.xml',
        'report/event_gratuity_report.xml',
        'report/event_coordinator_report.xml',
        'report/event_callout_report.xml',
        'report/event_payment_report.xml',
        'views/event_checklist_views.xml',
        'views/event_product_views.xml',
        'views/event_task_views.xml',
        'views/event_room_views.xml',
        'views/event_cost_type_views.xml',
        'views/hr_attendance_views.xml',
        'views/event_menus.xml',
        'views/website_event_form.xml',
        'views/event_terms_page.xml',
        'views/event_form_snippet.xml',
    ],
    'assets': {},
    'installable': True,
    'application': True,
    'auto_install': False,
}
