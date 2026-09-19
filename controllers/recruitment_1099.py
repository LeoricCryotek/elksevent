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
from odoo.http import request, Response
from werkzeug.exceptions import Forbidden, NotFound

from ..models.hr_applicant import WORK_DOC_TYPES, WORK_DOC_CODES

_WORK_DOC_LABELS = dict(WORK_DOC_TYPES)


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

        first = (post.get('first_name') or '').strip()
        last = (post.get('last_name') or '').strip()
        middle = (post.get('middle_initial') or '').strip()
        name = ' '.join(p for p in [first, middle, last] if p).strip()
        vals = {
            'partner_name': name or 'On-Call 1099 Applicant',
            'email_from': (post.get('email') or '').strip(),
            'partner_phone': (post.get('phone') or '').strip(),
            'job_id': job.id if job else False,
            'x_is_1099': True,
            'x_first_name': first,
            'x_middle_initial': middle[:1],
            'x_last_name': last,
            'x_other_last_names': (post.get('other_last_names') or '').strip(),
            'x_legal_name': name,
            'x_dob': _date(post.get('dob')),
            'x_business_name': (post.get('business_name') or '').strip(),
            'x_tin_type': (post.get('tin_type')
                           if post.get('tin_type') in ('ssn', 'ein')
                           else 'ssn'),
            'x_ssn': (post.get('ssn') or '').strip(),
            'x_addr_street': (post.get('addr_street') or '').strip(),
            'x_addr_apt': (post.get('addr_apt') or '').strip(),
            'x_addr_city': (post.get('addr_city') or '').strip(),
            'x_addr_state': (post.get('addr_state') or '').strip(),
            'x_addr_zip': (post.get('addr_zip') or '').strip(),
            'x_work_auth': (post.get('work_auth')
                            if post.get('work_auth') in (
                                'citizen', 'noncitizen_national',
                                'permanent_resident', 'authorized_alien')
                            else False),
            'x_uscis_number': (post.get('uscis_number')
                               or post.get('uscis_number_alien')
                               or '').strip(),
            'x_i94_number': (post.get('i94_number') or '').strip(),
            'x_foreign_passport': (post.get('foreign_passport') or '').strip(),
            'x_work_auth_expiry': _date(post.get('work_auth_expiry')),
            'x_w9_tax_class': (post.get('w9_tax_class')
                               if post.get('w9_tax_class') in (
                                   'individual', 'c_corp', 's_corp',
                                   'partnership', 'trust_estate', 'llc',
                                   'other')
                               else False),
            'x_w9_llc_class': (post.get('w9_llc_class')
                               if post.get('w9_llc_class') in ('C', 'S', 'P')
                               else False),
            'x_w9_other_desc': (post.get('w9_other_desc') or '').strip(),
            'x_work_doc_type': (post.get('work_doc_type')
                                if post.get('work_doc_type') in WORK_DOC_CODES
                                else False),
            'x_not_backup_withholding': bool(post.get('not_backup_withholding')),
            'x_i9_signed': bool(post.get('attest')),
            'x_signature_name': (post.get('signature_name') or '').strip(),
            'x_signature_date': _date(post.get('signature_date'))
            or datetime.today().date(),
        }

        # Everything the official W-9 + I-9 need must be present. Validate
        # against the same rules the forms use; on any gap, re-render the form
        # with the errors and the values the applicant already typed so nothing
        # is lost.
        draft = request.env['hr.applicant'].sudo().new(vals)
        missing = draft._onboarding_missing_fields()
        if missing:
            return request.render('elksevent.onboard_1099_form', {
                'errors': missing,
                'vals': post,
            })

        files = request.httprequest.files
        # Label the second document by the type the applicant picked, so the
        # attachment reads "U.S. Passport - ..." or "Green Card - ..." rather
        # than always saying "SSN". Fall back to a generic label if unset.
        work_doc_full = _WORK_DOC_LABELS.get(vals['x_work_doc_type'], '')
        work_doc_label = (work_doc_full.split(' (')[0] if work_doc_full
                          else 'Work-Authorization Document')
        doc_map = [
            ('id_doc', 'x_id_doc', 'x_id_doc_name', 'Photo ID (front)'),
            ('id_doc_back', 'x_id_doc_back', 'x_id_doc_back_name',
             'Photo ID (back)'),
            ('ssn_doc', 'x_ssn_doc', 'x_ssn_doc_name', work_doc_label),
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

    # ------------------------------------------------------------------
    # Filled official-form downloads (staff only). The backend "Print W-9"
    # / "Print I-9" buttons open these; they stream the real government
    # PDF filled with the applicant's data.
    # ------------------------------------------------------------------
    def _get_applicant_for_staff(self, app_id):
        user = request.env.user
        allowed = (
            user.has_group('hr_recruitment.group_hr_recruitment_user')
            or user.has_group('hr.group_hr_user')
            or user.has_group('elksevent.group_event_coordinator'))
        if not allowed:
            raise Forbidden()
        applicant = request.env['hr.applicant'].browse(app_id).exists()
        if not applicant:
            raise NotFound()
        return applicant

    def _stream_pdf(self, data, filename):
        return Response(data, headers=[
            ('Content-Type', 'application/pdf'),
            ('Content-Length', str(len(data))),
            ('Content-Disposition', 'inline; filename="%s"' % filename),
        ])

    @http.route('/elks/applicant/<int:app_id>/w9.pdf', type='http',
                auth='user', website=False)
    def applicant_w9_pdf(self, app_id, **kw):
        applicant = self._get_applicant_for_staff(app_id)
        data = applicant._official_pdf_bytes('w9')
        fname = 'W-9 - %s.pdf' % (applicant.x_legal_name
                                  or applicant.partner_name or 'applicant')
        return self._stream_pdf(data, fname)

    @http.route('/elks/applicant/<int:app_id>/i9.pdf', type='http',
                auth='user', website=False)
    def applicant_i9_pdf(self, app_id, **kw):
        applicant = self._get_applicant_for_staff(app_id)
        data = applicant._official_pdf_bytes('i9')
        fname = 'I-9 - %s.pdf' % (applicant.x_legal_name
                                  or applicant.partner_name or 'applicant')
        return self._stream_pdf(data, fname)

    @http.route('/elks/applicant/<int:app_id>/employment_file.pdf',
                type='http', auth='user', website=False)
    def applicant_employment_file_pdf(self, app_id, **kw):
        applicant = self._get_applicant_for_staff(app_id)
        data = applicant._employment_file_pdf_bytes()
        fname = 'Employment File - %s.pdf' % (
            applicant.x_legal_name or applicant.partner_name or 'applicant')
        return self._stream_pdf(data, fname)
