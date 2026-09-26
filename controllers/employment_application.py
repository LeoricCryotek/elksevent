# -*- coding: utf-8 -*-
"""Public 'Apply for Employment' packet form (W-2 hire).

Collects the full Lodge Employment Application, I-9 Section 1, W-4 and Direct
Deposit, then creates an hr.applicant (x_is_employment_app) with the repeating
education / reference / prior-employer rows. HR prints the whole packet and
one-click creates the employee.
"""
from datetime import datetime

from odoo import http
from odoo.http import request


class EmploymentApplication(http.Controller):

    @http.route('/elks/apply', type='http', auth='public', website=True,
                sitemap=True)
    def employment_form(self, **kw):
        return request.render('elksevent.employment_application_form', {
            'vals': {}, 'errors': []})

    @http.route('/elks/apply/submit', type='http', auth='public',
                website=True, csrf=True, methods=['POST'])
    def employment_submit(self, **post):
        job = request.env.ref('elksevent.job_employment',
                              raise_if_not_found=False)

        def _date(v):
            try:
                return datetime.strptime(v, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                return False

        def g(k):
            return (post.get(k) or '').strip()

        first, middle, last = g('first_name'), g('middle_initial'), g('last_name')
        name = ' '.join(p for p in [first, middle, last] if p).strip()

        errors = []
        for field, label in [(first, 'First name'), (last, 'Last name'),
                             (g('email'), 'Email'), (g('phone'), 'Phone'),
                             (g('position'), 'Position applied for'),
                             (g('signature_name'), 'Signature')]:
            if not field:
                errors.append(label)
        if not post.get('attest'):
            errors.append('Signature attestation')
        if errors:
            return request.render('elksevent.employment_application_form', {
                'vals': post, 'errors': errors})

        wa = g('work_auth')
        vals = {
            'partner_name': name or 'Employment Applicant',
            'email_from': g('email'),
            'partner_phone': g('phone'),
            'job_id': job.id if job else False,
            'x_is_employment_app': True,
            'x_first_name': first, 'x_middle_initial': middle[:1],
            'x_last_name': last, 'x_legal_name': name,
            'x_addr_street': g('addr_street'), 'x_addr_apt': g('addr_apt'),
            'x_addr_city': g('addr_city'), 'x_addr_state': g('addr_state'),
            'x_addr_zip': g('addr_zip'), 'x_dob': _date(post.get('dob')),
            'x_ssn': g('ssn'),
            'x_date_available': _date(post.get('date_available')),
            'x_desired_salary': g('desired_salary'),
            'x_position_applied': g('position'),
            'x_is_citizen': bool(post.get('is_citizen')),
            'x_worked_here_before': bool(post.get('worked_before')),
            'x_worked_here_when': g('worked_when'),
            'x_felony': bool(post.get('felony')),
            'x_felony_explain': g('felony_explain'),
            'x_work_auth': wa if wa in (
                'citizen', 'noncitizen_national', 'permanent_resident',
                'authorized_alien') else False,
            'x_uscis_number': g('uscis_number'),
            'x_work_auth_expiry': _date(post.get('work_auth_expiry')),
            'x_mil_branch': g('mil_branch'), 'x_mil_from': g('mil_from'),
            'x_mil_to': g('mil_to'), 'x_mil_rank': g('mil_rank'),
            'x_mil_discharge': g('mil_discharge'),
            'x_mil_explain': g('mil_explain'),
            'x_w4_filing': (post.get('w4_filing')
                            if post.get('w4_filing') in (
                                'single', 'married', 'hoh') else False),
            'x_w4_multiple_jobs': bool(post.get('w4_multiple_jobs')),
            'x_w4_dependents_amt': g('w4_dependents'),
            'x_w4_other_income': g('w4_other_income'),
            'x_w4_deductions': g('w4_deductions'),
            'x_w4_extra_withholding': g('w4_extra'),
            'x_dd_bank_name': g('dd_bank_name'),
            'x_dd_bank_address': g('dd_bank_address'),
            'x_dd_account_type': (post.get('dd_account_type')
                                  if post.get('dd_account_type') in (
                                      'checking', 'savings') else False),
            'x_dd_routing': g('dd_routing'), 'x_dd_account': g('dd_account'),
            'x_dd_authorized': bool(post.get('dd_authorized')),
            'x_i9_signed': bool(post.get('attest')),
            'x_signature_name': g('signature_name'),
            'x_signature_date': _date(post.get('signature_date'))
            or datetime.today().date(),
        }

        applicant = request.env['hr.applicant'].sudo().create(vals)

        # Repeating rows.
        Edu = request.env['elks.job.education'].sudo()
        i = 0
        while ('edu_school_%d' % i) in post or ('edu_level_%d' % i) in post:
            school = g('edu_school_%d' % i)
            if school:
                Edu.create({
                    'applicant_id': applicant.id,
                    'level': (post.get('edu_level_%d' % i)
                              if post.get('edu_level_%d' % i) in (
                                  'hs', 'college', 'other') else 'other'),
                    'school': school, 'address': g('edu_addr_%d' % i),
                    'date_from': g('edu_from_%d' % i),
                    'date_to': g('edu_to_%d' % i),
                    'graduated': bool(post.get('edu_grad_%d' % i)),
                    'degree': g('edu_degree_%d' % i)})
            i += 1

        Ref = request.env['elks.job.reference'].sudo()
        i = 0
        while ('ref_name_%d' % i) in post:
            rn = g('ref_name_%d' % i)
            if rn:
                Ref.create({
                    'applicant_id': applicant.id, 'name': rn,
                    'relationship': g('ref_rel_%d' % i),
                    'company': g('ref_company_%d' % i),
                    'phone': g('ref_phone_%d' % i),
                    'address': g('ref_addr_%d' % i)})
            i += 1

        Emp = request.env['elks.job.prior.employer'].sudo()
        i = 0
        while ('emp_company_%d' % i) in post:
            comp = g('emp_company_%d' % i)
            if comp:
                Emp.create({
                    'applicant_id': applicant.id, 'company': comp,
                    'phone': g('emp_phone_%d' % i),
                    'address': g('emp_addr_%d' % i),
                    'supervisor': g('emp_supervisor_%d' % i),
                    'title': g('emp_title_%d' % i),
                    'salary_start': g('emp_salstart_%d' % i),
                    'salary_end': g('emp_salend_%d' % i),
                    'responsibilities': g('emp_resp_%d' % i),
                    'date_from': g('emp_from_%d' % i),
                    'date_to': g('emp_to_%d' % i),
                    'reason_leaving': g('emp_reason_%d' % i),
                    'may_contact': bool(post.get('emp_contact_%d' % i))})
            i += 1

        # ID / document uploads (optional) -> sidebar attachments.
        files = request.httprequest.files
        Att = request.env['ir.attachment'].sudo()
        for field, label in [('id_doc', 'Photo ID'),
                             ('void_check', 'Voided Check / Bank Letter')]:
            f = files.get(field)
            if f and f.filename:
                data = f.read()
                if data:
                    import base64
                    Att.create({
                        'name': '%s - %s' % (label, f.filename),
                        'datas': base64.b64encode(data),
                        'res_model': 'hr.applicant',
                        'res_id': applicant.id, 'res_field': False})

        applicant.message_post(body=(
            "<b>Employment application submitted from the website.</b><br/>"
            "Name: %s | Email: %s | Position: %s"
        ) % (name, vals['email_from'], vals['x_position_applied']))
        return request.render('elksevent.employment_application_thanks',
                              {'applicant': applicant})
