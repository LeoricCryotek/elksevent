# -*- coding: utf-8 -*-
"""Remove the base "Timesheets" report from the event (project.task) Print menu.

Events don't use manual timesheets, so the stock hr_timesheet "Timesheets"
print action just clutters the gear menu. Clear its project.task binding. This
touches only the timesheet report bound to project.task - our own event reports
(Coordinator, Gratuity, Facility Usage Agreement, Event P&L) are left alone.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    reports = env['ir.actions.report'].search([('model', '=', 'project.task')])
    for r in reports:
        name = (r.name or '').lower()
        rname = (r.report_name or '').lower()
        if 'timesheet' in rname or name == 'timesheets':
            if r.binding_model_id:
                r.binding_model_id = False
