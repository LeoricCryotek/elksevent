# -*- coding: utf-8 -*-
"""1099 contractor onboarding on the recruitment applicant.

HUMAN
-----
People can "Join as a 1099" from the website careers page. They complete an
I-9 / W-9 style form on their phone and upload a photo ID and their SSN card
(or other work-authorization document). Recruitment reviews it; once approved,
one click creates their employee record already marked as a 1099 contractor so
payroll knows how to pay them and the disbursement sheet flags them.

AI
--
- Extends hr.applicant with the onboarding fields + two document binaries.
- action_create_1099_employee(): make the hr.employee (pay category 1099),
  copy the documents across, and open it.
"""
from odoo import _, fields, models
from odoo.exceptions import UserError


class HrApplicant(models.Model):
    _inherit = 'hr.applicant'

    x_is_1099 = fields.Boolean(
        "1099 Contractor Application",
        help="This application came in through the 'Join as a 1099' onboarding "
             "form and carries the I-9 / W-9 details below.")

    # Identity / W-9
    x_legal_name = fields.Char("Legal Full Name")
    x_dob = fields.Date("Date of Birth")
    x_business_name = fields.Char(
        "Business Name (if any)",
        help="If paid as a business/DBA rather than an individual.")
    x_tin_type = fields.Selection([
        ('ssn', 'SSN'), ('ein', 'EIN')], string="Taxpayer ID Type",
        default='ssn')
    x_ssn = fields.Char(
        "SSN / EIN (Taxpayer ID)",
        help="Full taxpayer ID for the W-9 / 1099. Sensitive — visible to "
             "recruitment/HR only.")
    # W-9 federal tax classification (how the payee is taxed).
    x_w9_tax_class = fields.Selection([
        ('individual', 'Individual / Sole Proprietor / Single-member LLC'),
        ('c_corp', 'C Corporation'),
        ('s_corp', 'S Corporation'),
        ('partnership', 'Partnership'),
        ('trust_estate', 'Trust / Estate'),
        ('llc', 'LLC (C/S/Partnership)'),
        ('other', 'Other'),
    ], string="W-9 Tax Classification")
    x_not_backup_withholding = fields.Boolean(
        "Certified: NOT subject to backup withholding",
        help="The payee certified on the W-9 that they are not subject to "
             "backup withholding (Form W-9, Part II).")

    # Address
    x_addr_street = fields.Char("Street Address")
    x_addr_city = fields.Char("City")
    x_addr_state = fields.Char("State")
    x_addr_zip = fields.Char("ZIP")

    # I-9 work authorization
    x_work_auth = fields.Selection([
        ('citizen', 'U.S. Citizen'),
        ('noncitizen_national', 'Noncitizen National of the U.S.'),
        ('permanent_resident', 'Lawful Permanent Resident'),
        ('authorized_alien', 'Alien Authorized to Work'),
    ], string="Work Authorization (I-9)")
    x_uscis_number = fields.Char("USCIS / A-Number (if applicable)")
    x_work_auth_expiry = fields.Date("Work Auth. Expiration (if any)")

    # Attestation / signature
    x_i9_signed = fields.Boolean("I-9 / W-9 Attestation Signed")
    x_signature_name = fields.Char("Signature (typed legal name)")
    x_signature_date = fields.Date("Signed On")

    # Uploaded documents
    x_id_doc = fields.Binary("Photo ID (front)", attachment=True)
    x_id_doc_name = fields.Char("Photo ID filename")
    x_id_doc_back = fields.Binary("Photo ID (back)", attachment=True)
    x_id_doc_back_name = fields.Char("Photo ID back filename")
    x_ssn_doc = fields.Binary(
        "SSN Card / Work-Auth Document", attachment=True)
    x_ssn_doc_name = fields.Char("SSN document filename")

    def action_create_1099_employee(self):
        """Create the hr.employee for an approved 1099 applicant, marked as a
        1099 contractor, carrying the onboarding documents."""
        self.ensure_one()
        Employee = self.env['hr.employee']
        name = self.x_legal_name or self.partner_name
        if not name:
            raise UserError(_("Enter the contractor's legal name first."))
        emp = Employee.create({
            'name': name,
            'work_email': self.email_from or False,
            'work_phone': self.partner_phone or False,
            'x_pay_category': '1099',
            'x_tin_type': self.x_tin_type or 'ssn',
            'x_tin_last4': (self.x_ssn or '')[-4:] or False,
            'x_w9_on_file': True,
        })
        # Private address / DOB / SSN / contract type — best effort (field
        # names vary by Odoo build). Wage is left blank on purpose: 1099 pay
        # comes from the event call-out rate on the timecard, not a base wage.
        extra = {
            'private_street': self.x_addr_street or False,
            'private_city': self.x_addr_city or False,
            'private_zip': self.x_addr_zip or False,
            'birthday': self.x_dob or False,
            # SSN (or EIN) onto the standard employee field for payroll/1099.
            'ssnid': self.x_ssn or False,
        }
        # Mark as an independent contractor / freelance where the selection has
        # that value.
        if 'employee_type' in emp._fields:
            sel = dict(emp._fields['employee_type'].selection or [])
            if 'freelance' in sel:
                extra['employee_type'] = 'freelance'
            elif 'contractor' in sel:
                extra['employee_type'] = 'contractor'
        # Contract Type = "1099 Contractor" (find or create).
        if 'contract_type_id' in emp._fields and 'hr.contract.type' in self.env:
            CT = self.env['hr.contract.type'].sudo()
            ct = CT.search([('name', '=', '1099 Contractor')], limit=1) \
                or CT.create({'name': '1099 Contractor'})
            extra['contract_type_id'] = ct.id
        extra = {k: v for k, v in extra.items()
                 if v and k in emp._fields}
        if extra:
            try:
                emp.write(extra)
            except Exception:  # noqa: BLE001
                pass
        # W-9 details that have no standard employee field — record on the
        # employee's chatter so payroll has them.
        wc = dict(self._fields['x_w9_tax_class'].selection).get(
            self.x_w9_tax_class, '')
        emp.message_post(body=_(
            "W-9 / 1099 onboarding: TIN type %(tt)s, tax classification "
            "%(cls)s, backup withholding: %(bw)s.",
            tt=(self.x_tin_type or 'ssn').upper(),
            cls=wc or 'n/a',
            bw=_("not subject (certified)") if self.x_not_backup_withholding
            else _("not certified")))
        # Copy the uploaded documents onto the employee as regular (sidebar)
        # attachments. Prefer the viewable sidebar copies (res_field=False);
        # fall back to the binary-field attachments for older applications.
        Att = self.env['ir.attachment'].sudo()
        side = Att.search([
            ('res_model', '=', 'hr.applicant'), ('res_id', '=', self.id),
            ('res_field', '=', False),
        ])
        if not side:
            side = Att.search([
                ('res_model', '=', 'hr.applicant'), ('res_id', '=', self.id),
                ('res_field', 'in',
                 ('x_id_doc', 'x_id_doc_back', 'x_ssn_doc')),
            ])
        for att in side:
            att.copy({
                'res_model': 'hr.employee',
                'res_id': emp.id,
                'res_field': False,
                'name': att.name,
            })
        # Match free-text roster entries (outside help typed by name) to this
        # new employee, so their past/future call-out lines link to the record
        # and their event pay flows to their timecard.
        if 'elks.event.callout.line' in self.env and emp.name:
            Line = self.env['elks.event.callout.line'].sudo()
            target = emp.name.strip().lower()
            for ln in Line.search([('employee_id', '=', False),
                                    ('person_name', '!=', False)]):
                if (ln.person_name or '').strip().lower() == target:
                    ln.write({'employee_id': emp.id, 'person_name': False})

        self.message_post(body=_(
            "Created 1099 employee %s from this application.", emp.name))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'hr.employee',
            'res_id': emp.id,
            'view_mode': 'form',
            'target': 'current',
        }
