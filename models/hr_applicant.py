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
        name = self.x_legal_name or self.partner_name or self.name
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
        # Private address / DOB — best effort (field names vary by Odoo build).
        extra = {
            'private_street': self.x_addr_street or False,
            'private_city': self.x_addr_city or False,
            'private_zip': self.x_addr_zip or False,
            'birthday': self.x_dob or False,
        }
        extra = {k: v for k, v in extra.items()
                 if v and k in emp._fields}
        if extra:
            try:
                emp.write(extra)
            except Exception:  # noqa: BLE001
                pass
        # Copy the uploaded document attachments onto the employee.
        atts = self.env['ir.attachment'].sudo().search([
            ('res_model', '=', 'hr.applicant'),
            ('res_id', '=', self.id),
            ('res_field', 'in',
             ('x_id_doc', 'x_id_doc_back', 'x_ssn_doc')),
        ])
        for att in atts:
            att.copy({
                'res_model': 'hr.employee',
                'res_id': emp.id,
                'res_field': False,
                'name': att.name,
            })
        self.message_post(body=_(
            "Created 1099 employee %s from this application.", emp.name))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'hr.employee',
            'res_id': emp.id,
            'view_mode': 'form',
            'target': 'current',
        }
