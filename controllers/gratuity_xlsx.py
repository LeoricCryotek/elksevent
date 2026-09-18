# -*- coding: utf-8 -*-
"""Gratuity / labor disbursement worksheet (Excel) for the bookkeeper.

HUMAN
-----
A spreadsheet listing every person who worked the event's certified call-outs:
who to pay, whether they're a 1099 (outside) payee or a lodge employee, their
labor pay (hours x rate), their tips (gratuity share), and the check total.
1099 payees are listed first so the bookkeeper knows who needs a 1099 check.

AI
--
- Route builds an .xlsx with xlsxwriter and streams it. Payees are aggregated
  across all certified departments by employee (or free-text name).
"""
import io
from urllib.parse import quote

import xlsxwriter

from odoo import http
from odoo.http import request


class GratuityDisbursementXlsx(http.Controller):

    @http.route('/elksevent/gratuity_xlsx/<int:event_id>',
                type='http', auth='user')
    def gratuity_xlsx(self, event_id, **kw):
        env = request.env
        if not env.user.has_group('elksevent.group_event_coordinator'):
            return request.not_found()
        event = env['project.task'].sudo().browse(event_id)
        if not event.exists():
            return request.not_found()

        settings = env['elks.lodge.settings'].sudo().search([], limit=1)
        lodge = settings.name if settings else 'Elks Lodge'
        if settings and settings.lodge_number:
            lodge = '%s #%s' % (lodge, settings.lodge_number)

        # Aggregate payees across every certified call-out.
        payees = {}
        for co in event.x_callout_ids.filtered(lambda c: c.state == 'certified'):
            for ln in co.line_ids:
                if not (ln.employee_id or ln.person_name):
                    continue
                if ln.employee_id:
                    key = ('emp', ln.employee_id.id)
                    name = ln.employee_id.name
                else:
                    name = (ln.person_name or '').strip()
                    key = ('name', name.lower())
                p = payees.setdefault(key, {
                    'name': name, 'is_1099': not bool(ln.employee_id),
                    'depts': set(), 'hours': 0.0, 'pay': 0.0, 'tips': 0.0})
                p['depts'].add(co.department_label)
                p['hours'] += ln.hours or 0.0
                p['pay'] += ln.raw_cost or 0.0
                p['tips'] += ln.gratuity_share or 0.0

        rows = sorted(payees.values(),
                      key=lambda p: (not p['is_1099'], p['name'].lower()))

        out = io.BytesIO()
        wb = xlsxwriter.Workbook(out, {'in_memory': True})
        ws = wb.add_worksheet('Disbursement')

        money = wb.add_format({'num_format': '$#,##0.00'})
        money_b = wb.add_format({'num_format': '$#,##0.00', 'bold': True})
        title = wb.add_format({'bold': True, 'font_size': 14})
        sub = wb.add_format({'font_size': 10, 'font_color': '#666666'})
        hdr = wb.add_format({
            'bold': True, 'font_color': 'white', 'bg_color': '#46166A',
            'border': 1, 'align': 'center', 'valign': 'vcenter'})
        cell = wb.add_format({'border': 1})
        cell_c = wb.add_format({'border': 1, 'align': 'center'})
        num = wb.add_format({'border': 1, 'num_format': '0.00'})
        cur = wb.add_format({'border': 1, 'num_format': '$#,##0.00'})
        tot = wb.add_format({'bold': True, 'border': 1, 'bg_color': '#EFE9F5'})
        tot_c = wb.add_format({
            'bold': True, 'border': 1, 'bg_color': '#EFE9F5',
            'num_format': '$#,##0.00'})
        tag1099 = wb.add_format({'border': 1, 'align': 'center',
                                 'bold': True, 'font_color': '#B02A37'})

        ws.set_column(0, 0, 26)   # Payee
        ws.set_column(1, 1, 14)   # Type
        ws.set_column(2, 2, 22)   # Departments
        ws.set_column(3, 3, 9)    # Hours
        ws.set_column(4, 6, 13)   # Pay / Tips / Total

        ws.merge_range(0, 0, 0, 6, '%s - Gratuity & Labor Disbursement' % lodge,
                       title)
        ev_date = event.x_event_date and event.x_event_date.strftime(
            '%m/%d/%Y') or ''
        ws.merge_range(1, 0, 1, 6,
                       'Event: %s   %s' % (event.name or '', ev_date), sub)
        ws.merge_range(2, 0, 2, 6,
                       '1099 (outside) payees are listed first. Pay = hours x '
                       'rate; Tips = gratuity share; cut each check for the '
                       'Total.', sub)

        r = 4
        headers = ['Payee', 'Type', 'Department(s)', 'Hours',
                   'Pay', 'Tips', 'Check Total']
        for c, h in enumerate(headers):
            ws.write(r, c, h, hdr)
        r += 1

        t_hours = t_pay = t_tips = t_total = 0.0
        for p in rows:
            total = p['pay'] + p['tips']
            ws.write(r, 0, p['name'], cell)
            ws.write(r, 1, '1099' if p['is_1099'] else 'Employee (W-2)',
                     tag1099 if p['is_1099'] else cell_c)
            ws.write(r, 2, ', '.join(sorted(p['depts'])), cell)
            ws.write_number(r, 3, round(p['hours'], 2), num)
            ws.write_number(r, 4, round(p['pay'], 2), cur)
            ws.write_number(r, 5, round(p['tips'], 2), cur)
            ws.write_number(r, 6, round(total, 2), cur)
            t_hours += p['hours']
            t_pay += p['pay']
            t_tips += p['tips']
            t_total += total
            r += 1

        if not rows:
            ws.merge_range(r, 0, r, 6,
                           'No certified call-out roster with pay/tips yet.',
                           cell)
            r += 1
        else:
            ws.write(r, 0, 'TOTALS', tot)
            ws.write(r, 1, '', tot)
            ws.write(r, 2, '', tot)
            ws.write_number(r, 3, round(t_hours, 2), tot_c)
            ws.write_number(r, 4, round(t_pay, 2), tot_c)
            ws.write_number(r, 5, round(t_tips, 2), tot_c)
            ws.write_number(r, 6, round(t_total, 2), tot_c)

        wb.close()
        data = out.getvalue()
        out.close()

        fname = 'Gratuity Disbursement - %s.xlsx' % (event.name or 'Event')
        disposition = "attachment; filename*=UTF-8''%s" % quote(fname)
        return request.make_response(data, headers=[
            ('Content-Type',
             'application/vnd.openxmlformats-officedocument.'
             'spreadsheetml.sheet'),
            ('Content-Disposition', disposition),
        ])
