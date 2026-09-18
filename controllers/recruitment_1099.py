# -*- coding: utf-8 -*-
"""Public 'Join as a 1099' onboarding form (mobile-friendly).

HUMAN
-----
Linked from the careers page (/jobs). A prospective contractor completes the
I-9 / W-9 details on their phone, uploads a photo ID and their SSN card (or
work-authorization document), and submits. It lands as a recruitment applicant
for review; once approved, HR creates them as a 1099 employee in one click.

AI
--
- GET /elks/onboard/1099 renders the form; POST creates hr.applicant (sudo)
  with x_is_1099 + the onboarding fields and the two/three document binaries.
- Files come from request.httprequest.files; stored base64 into Binary fields.
"""
import base64
from datetime import datetime

from odoo import http
from odoo.http import request


class Recruitment1099(http.Controller):

    @http.route('/elks/onboard/1099', type='http', auth='public',
                website=True, sitemap=True)
    def onboard_1099_form(self, **kw):
        return request.render('elksevent.onboard_1099_form', {})

    @http.route('/elks/onboard/1099/submit', type='http', auth='public',
                website=True, csrf=True, methods=['POST'])
    def onboard_1099_submit(self, **post):
        job = request.env.ref('elksevent.job_1099_contractor',
                              raise_if_not_found=False)

        def _date(v):
            try:
                return datetime.strptime(v, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                return False

        name = (post.get('legal_name') or '').strip()
        vals = {
            'partner_name': name or 'On-Call 1099 Applicant',
            'email_from': (post.get('email') or '').strip(),
            'partner_phone': (post.get('phone') or '').strip(),
            'job_id': job.id if job else False,
            'x_is_1099': True,
            'x_legal_name': name,
            'x_dob': _date(post.get('dob')),
            'x_business_name': (post.get('business_name') or '').strip(),
            'x_tin_type': (post.get('tin_type')
                           if post.get('tin_type') in ('ssn', 'ein')
                           else 'ssn'),
            'x_ssn': (post.get('ssn') or '').strip(),
            'x_addr_street': (post.get('addr_street') or '').strip(),
            'x_addr_city': (post.get('addr_city') or '').strip(),
            'x_addr_state': (post.get('addr_state') or '').strip(),
            'x_addr_zip': (post.get('addr_zip') or '').strip(),
            'x_work_auth': (post.get('work_auth')
                            if post.get('work_auth') in (
                                'citizen', 'noncitizen_national',
                                'permanent_resident', 'authorized_alien')
                            else False),
            'x_uscis_number': (post.get('uscis_number') or '').strip(),
            'x_work_auth_expiry': _date(post.get('work_auth_expiry')),
            'x_w9_tax_class': (post.get('w9_tax_class')
                               if post.get('w9_tax_class') in (
                                   'individual', 'c_corp', 's_corp',
                                   'partnership', 'trust_estate', 'llc',
                                   'other')
                               else False),
            'x_not_backup_withholding': bool(post.get('not_backup_withholding')),
            'x_i9_signed': bool(post.get('attest')),
            'x_signature_name': (post.get('signature_name') or '').strip(),
            'x_signature_date': _date(post.get('signature_date'))
            or datetime.today().date(),
        }

        files = request.httprequest.files
        doc_map = [
            ('id_doc', 'x_id_doc', 'x_id_doc_name', 'Photo ID (front)'),
            ('id_doc_back', 'x_id_doc_back', 'x_id_doc_back_name',
             'Photo ID (back)'),
            ('ssn_doc', 'x_ssn_doc', 'x_ssn_doc_name',
             'SSN / Work-Auth Document'),
        ]
        uploaded = []
        for field, bin_f, name_f, label in doc_map:
            f = files.get(field)
            if f and f.filename:
                data = f.read()
                if data:
                    b64 = base64.b64encode(data)
                    vals[bin_f] = b64
                    vals[name_f] = f.filename
                    uploaded.append((label, f.filename, b64))

        applicant = request.env['hr.applicant'].sudo().create(vals)

        # Attach the uploads as regular attachments (res_field=False) so they
        # appear in the application's attachment sidebar and can be previewed
        # and downloaded — not just as download-only binary fields.
        Att = request.env['ir.attachment'].sudo()
        for label, fname, b64 in uploaded:
            Att.create({
                'name': '%s - %s' % (label, fname),
                'datas': b64,
                'res_model': 'hr.applicant',
                'res_id': applicant.id,
                'res_field': False,
            })

        applicant.message_post(body=(
            "<b>1099 onboarding submitted from the website.</b><br/>"
            "Name: %s | Email: %s | Phone: %s"
        ) % (name, vals['email_from'], vals['partner_phone']))
        return request.render('elksevent.onboard_1099_thanks',
                              {'applicant': applicant})
