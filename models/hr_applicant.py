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
import base64
import io
import logging
import os

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


# Acceptable I-9 documents the applicant can upload as their SECOND document
# (alongside the photo ID). List A items establish BOTH identity and work
# authorization on their own; List C items establish work authorization and
# pair with the photo ID (List B) above. Kept as a module constant so the
# public controller validates against the exact same set.
WORK_DOC_TYPES = [
    ('us_passport',
     'U.S. Passport or Passport Card (List A - identity + work auth)'),
    ('perm_resident_card',
     'Permanent Resident Card / Green Card, Form I-551 (List A)'),
    ('ead_i766',
     'Employment Authorization Document, Form I-766 (List A)'),
    ('foreign_passport_i551',
     'Foreign Passport with temporary I-551 stamp / MRIV (List A)'),
    ('foreign_passport_i94',
     'Foreign Passport with Form I-94 / I-94A (List A)'),
    ('ssn_card',
     'Social Security Card, unrestricted (List C - work auth)'),
    ('birth_certificate',
     'U.S. Birth Certificate, certified copy (List C)'),
    ('birth_abroad',
     'Certification of Birth Abroad, FS-545 / DS-1350 / FS-240 (List C)'),
    ('tribal_doc',
     'Native American Tribal Document (List C)'),
    ('other_dhs',
     'Other DHS-issued work-authorization document (List C)'),
]
WORK_DOC_CODES = {code for code, _label in WORK_DOC_TYPES}


class HrApplicant(models.Model):
    _inherit = 'hr.applicant'

    x_is_1099 = fields.Boolean(
        "1099 Contractor Application",
        help="This application came in through the 'Join as a 1099' onboarding "
             "form and carries the I-9 / W-9 details below.")

    # Identity — split for the I-9 (which has separate name boxes). The W-9
    # uses the full assembled name on line 1.
    x_first_name = fields.Char("First Name (Given Name)")
    x_middle_initial = fields.Char("Middle Initial")
    x_last_name = fields.Char("Last Name (Family Name)")
    x_other_last_names = fields.Char("Other Last Names Used (if any)")
    x_legal_name = fields.Char(
        "Legal Full Name", compute="_compute_legal_name",
        store=True, readonly=False,
        help="Assembled from first / middle / last for the W-9 and the "
             "employee record. Editable if the legal name differs.")
    x_dob = fields.Date("Date of Birth")

    @api.depends('x_first_name', 'x_middle_initial', 'x_last_name')
    def _compute_legal_name(self):
        for rec in self:
            if rec.x_first_name or rec.x_last_name:
                parts = [rec.x_first_name or '',
                         (rec.x_middle_initial or '').strip(),
                         rec.x_last_name or '']
                rec.x_legal_name = ' '.join(p for p in parts if p).strip()
            # else: leave whatever is already there (legacy rows)
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
    x_w9_llc_class = fields.Selection([
        ('C', 'C — C corporation'),
        ('S', 'S — S corporation'),
        ('P', 'P — Partnership'),
    ], string="LLC Tax Classification",
        help="W-9 line 3a: for an LLC, the letter for how it is taxed "
             "(C, S, or P).")
    x_w9_other_desc = fields.Char(
        "Other Classification (describe)",
        help="W-9 line 3a: description when 'Other' is the tax "
             "classification.")
    x_not_backup_withholding = fields.Boolean(
        "Certified: NOT subject to backup withholding",
        help="The payee certified on the W-9 that they are not subject to "
             "backup withholding (Form W-9, Part II).")

    # Address
    x_addr_street = fields.Char("Street Address")
    x_addr_apt = fields.Char("Apt / Unit (if any)")
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
    x_i94_number = fields.Char("Form I-94 Admission Number (if applicable)")
    x_foreign_passport = fields.Char(
        "Foreign Passport Number & Country (if applicable)")
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
    # The second I-9 document: what kind it is, plus the upload. The renter
    # picks the type (Passport, SSN card, Green Card...) so it is filed and
    # labeled correctly instead of always reading "SSN card".
    x_work_doc_type = fields.Selection(
        WORK_DOC_TYPES, string="Work-Authorization Document Type")
    x_ssn_doc = fields.Binary(
        "Work-Authorization Document", attachment=True)
    x_ssn_doc_name = fields.Char("Work-Auth document filename")

    def _work_doc_label(self):
        """Short human label of the chosen second-document type (without the
        List A/C parenthetical) for filenames, chatter and the PDF."""
        self.ensure_one()
        full = dict(WORK_DOC_TYPES).get(self.x_work_doc_type, '')
        return full.split(' (')[0] if full else ''

    # ==================================================================
    # SECTION: Completeness — every field the official W-9 + I-9 need
    # HUMAN: The intake form must collect everything both forms require.
    #        This is the single source of truth for "what's required",
    #        used by the website submit handler to reject an incomplete
    #        application and by the backend to warn before printing.
    # ==================================================================
    def _onboarding_missing_fields(self):
        """Return a list of human labels for required fields still missing,
        given the applicant's own answers (conditionals included)."""
        self.ensure_one()
        missing = []
        req = [
            (self.x_first_name, "First name"),
            (self.x_last_name, "Last name"),
            (self.x_dob, "Date of birth"),
            (self.email_from, "Email"),
            (self.partner_phone, "Phone"),
            (self.x_addr_street, "Street address"),
            (self.x_addr_city, "City"),
            (self.x_addr_state, "State"),
            (self.x_addr_zip, "ZIP code"),
            (self.x_ssn, "Taxpayer ID (SSN/EIN)"),
            (self.x_tin_type, "Taxpayer ID type"),
            (self.x_w9_tax_class, "W-9 tax classification"),
            (self.x_work_auth, "Work authorization (I-9)"),
            (self.x_signature_name, "Signature (typed name)"),
            (self.x_i9_signed, "Attestation signature"),
        ]
        for val, label in req:
            if not val:
                missing.append(label)
        # Conditional — W-9 line 3a details
        if self.x_w9_tax_class == 'llc' and not self.x_w9_llc_class:
            missing.append("LLC tax classification (C/S/P)")
        if self.x_w9_tax_class == 'other' and not self.x_w9_other_desc:
            missing.append("Other classification description")
        # Conditional — I-9 work-authorization details
        if self.x_work_auth == 'permanent_resident' and not self.x_uscis_number:
            missing.append("USCIS / A-Number (permanent resident)")
        if self.x_work_auth == 'authorized_alien':
            if not self.x_work_auth_expiry:
                missing.append("Work authorization expiration date")
            if not (self.x_uscis_number or self.x_i94_number
                    or self.x_foreign_passport):
                missing.append(
                    "One of: A-Number, I-94 number, or foreign passport")
        return missing

    # ==================================================================
    # SECTION: Fill the EXACT official IRS/USCIS PDFs (AcroForm)
    # HUMAN: "Print W-9" / "Print I-9" fill the real government forms with
    #        this applicant's data — the same forms they signed online.
    # AI: Templates ship in static/src/pdf/. pypdf fills form fields; we set
    #     NeedAppearances so every viewer renders the values. Field names were
    #     read straight from the AcroForms (see the field maps below).
    # ==================================================================
    @staticmethod
    def _mmddyyyy(d):
        return d.strftime('%m/%d/%Y') if d else ''

    def _pdf_template_path(self, filename):
        # models/ -> module root -> static/src/pdf/<filename>
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, 'static', 'src', 'pdf', filename)

    def _fill_pdf(self, template_filename, text_vals, checkbox_states):
        """Fill an AcroForm PDF and return the bytes.

        text_vals: {field_full_name: str}
        checkbox_states: {field_full_name: on_state_name}  (e.g. '/1', '/On')
        """
        try:
            from pypdf import PdfReader, PdfWriter
            from pypdf.generic import NameObject, BooleanObject
        except ImportError:  # pragma: no cover - older runtimes
            from PyPDF2 import PdfReader, PdfWriter  # type: ignore
            from PyPDF2.generic import (  # type: ignore
                NameObject, BooleanObject)

        reader = PdfReader(self._pdf_template_path(template_filename))
        writer = PdfWriter()
        writer.append(reader)

        clean_text = {k: (v if v is not None else '')
                      for k, v in text_vals.items() if v not in (None, '')}
        for page in writer.pages:
            try:
                writer.update_page_form_field_values(
                    page, clean_text, auto_regenerate=False)
            except Exception:  # noqa: BLE001 - some pages have no fields
                pass

        if checkbox_states:
            for page in writer.pages:
                for annot in page.get('/Annots') or []:
                    obj = annot.get_object()
                    parts = []
                    nm = obj.get('/T')
                    if nm:
                        parts = [str(nm)]
                    parent = obj.get('/Parent')
                    while parent:
                        pobj = parent.get_object()
                        t = pobj.get('/T')
                        if t:
                            parts.insert(0, str(t))
                        parent = pobj.get('/Parent')
                    full = '.'.join(parts)
                    if full in checkbox_states:
                        on = checkbox_states[full]
                        obj[NameObject('/V')] = NameObject(on)
                        obj[NameObject('/AS')] = NameObject(on)

        # Make the values visible in every PDF viewer.
        try:
            writer.set_need_appearances_writer(True)
        except Exception:  # noqa: BLE001
            root = writer._root_object
            if '/AcroForm' in root:
                root['/AcroForm'][NameObject('/NeedAppearances')] = \
                    BooleanObject(True)

        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()

    def _w9_field_values(self):
        """Map applicant data onto the official 2024 Form W-9 fields."""
        self.ensure_one()
        P = 'topmostSubform[0].Page1[0].'
        text = {
            P + 'f1_01[0]': self.x_legal_name or '',        # line 1 name
            P + 'f1_02[0]': self.x_business_name or '',      # line 2 bus. name
            P + 'f1_07[0]': self.x_addr_street or '',        # line 5 address
            P + 'f1_08[0]': ', '.join(
                p for p in [self.x_addr_city or '',
                            ' '.join(x for x in [self.x_addr_state or '',
                                                 self.x_addr_zip or ''] if x)]
                if p),                                       # line 6 city/st/zip
        }
        checks = {}
        cls_box = {
            'individual': '/1', 'c_corp': '/2', 's_corp': '/3',
            'partnership': '/4', 'trust_estate': '/5', 'llc': '/6',
            'other': '/7',
        }
        box_field = {
            'individual': 'c1_1[0]', 'c_corp': 'c1_1[1]', 's_corp': 'c1_1[2]',
            'partnership': 'c1_1[3]', 'trust_estate': 'c1_1[4]',
            'llc': 'c1_1[5]', 'other': 'c1_1[6]',
        }
        if self.x_w9_tax_class in box_field:
            fld = (P + 'Boxes3a-b_ReadOrder[0].' + box_field[self.x_w9_tax_class])
            checks[fld] = cls_box[self.x_w9_tax_class]
        if self.x_w9_tax_class == 'llc' and self.x_w9_llc_class:
            text[P + 'Boxes3a-b_ReadOrder[0].f1_03[0]'] = self.x_w9_llc_class
        if self.x_w9_tax_class == 'other' and self.x_w9_other_desc:
            text[P + 'Boxes3a-b_ReadOrder[0].f1_04[0]'] = self.x_w9_other_desc
        # Part I — SSN (3-2-4) or EIN (2-7)
        digits = ''.join(c for c in (self.x_ssn or '') if c.isdigit())
        if self.x_tin_type == 'ein':
            if len(digits) >= 9:
                text[P + 'f1_14[0]'] = digits[:2]
                text[P + 'f1_15[0]'] = digits[2:9]
        else:
            if len(digits) >= 9:
                text[P + 'f1_11[0]'] = digits[:3]
                text[P + 'f1_12[0]'] = digits[3:5]
                text[P + 'f1_13[0]'] = digits[5:9]
        return text, checks

    def _i9_field_values(self):
        """Map applicant data onto the official Form I-9 (Section 1)."""
        self.ensure_one()
        state = (self.x_addr_state or '').strip().upper()
        # The I-9 State box is a dropdown of 2-letter codes; only send a valid
        # one, otherwise leave it blank for the reviewer.
        valid_states = {
            'AK', 'AL', 'AR', 'AS', 'AZ', 'CA', 'CO', 'CT', 'DC', 'DE', 'FL',
            'GA', 'GU', 'HI', 'IA', 'ID', 'IL', 'IN', 'KS', 'KY', 'LA', 'MA',
            'MD', 'ME', 'MI', 'MN', 'MO', 'MP', 'MS', 'MT', 'NC', 'ND', 'NE',
            'NH', 'NJ', 'NM', 'NV', 'NY', 'OH', 'OK', 'OR', 'PA', 'PR', 'RI',
            'SC', 'SD', 'TN', 'TX', 'UT', 'VA', 'VI', 'VT', 'WA', 'WI', 'WV',
            'WY',
        }
        ssn_digits = ''.join(c for c in (self.x_ssn or '') if c.isdigit())
        text = {
            'Last Name (Family Name)': self.x_last_name or '',
            'First Name Given Name': self.x_first_name or '',
            'Employee Middle Initial (if any)':
                (self.x_middle_initial or '')[:1],
            'Employee Other Last Names Used (if any)':
                self.x_other_last_names or 'N/A',
            'Address Street Number and Name': self.x_addr_street or '',
            'Apt Number (if any)': self.x_addr_apt or 'N/A',
            'City or Town': self.x_addr_city or '',
            'ZIP Code': self.x_addr_zip or '',
            'Date of Birth mmddyyyy': self._mmddyyyy(self.x_dob),
            # I-9 SSN box is a 9-digit comb — digits only, no dashes.
            'US Social Security Number':
                ssn_digits[:9] if self.x_tin_type != 'ein' else '',
            'Employees E-mail Address': self.email_from or 'N/A',
            'Telephone Number': self.partner_phone or 'N/A',
            'Signature of Employee': self.x_signature_name or '',
            "Today's Date mmddyyy": self._mmddyyyy(
                self.x_signature_date or fields.Date.context_today(self)),
        }
        if state in valid_states:
            text['State'] = state
        checks = {}
        cb = {'citizen': 'CB_1', 'noncitizen_national': 'CB_2',
              'permanent_resident': 'CB_3', 'authorized_alien': 'CB_4'}
        if self.x_work_auth in cb:
            checks[cb[self.x_work_auth]] = '/On'
        if self.x_work_auth == 'permanent_resident':
            text['3 A lawful permanent resident Enter USCIS or ANumber'] = \
                self.x_uscis_number or ''
        if self.x_work_auth == 'authorized_alien':
            text['Exp Date mmddyyyy'] = self._mmddyyyy(self.x_work_auth_expiry)
            if self.x_uscis_number:
                text['USCIS ANumber'] = self.x_uscis_number
            if self.x_i94_number:
                text['Form I94 Admission Number'] = self.x_i94_number
            if self.x_foreign_passport:
                text['Foreign Passport Number and Country of IssuanceRow1'] = \
                    self.x_foreign_passport
        return text, checks

    def _official_pdf_bytes(self, kind):
        self.ensure_one()
        if kind == 'w9':
            text, checks = self._w9_field_values()
            return self._fill_pdf('w9_blank.pdf', text, checks)
        text, checks = self._i9_field_values()
        return self._fill_pdf('i9_blank.pdf', text, checks)

    def action_print_w9_official(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': '/elks/applicant/%s/w9.pdf' % self.id,
            'target': 'new',
        }

    def action_print_i9_official(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': '/elks/applicant/%s/i9.pdf' % self.id,
            'target': 'new',
        }

    # ==================================================================
    # SECTION: Whole employment file — one PDF to print for the folder
    # HUMAN: "Print File" stacks the filled W-9, the filled I-9, and the
    #        uploaded photo ID and SSN / work-auth document into a single
    #        PDF, so the whole records packet prints in one go.
    # ==================================================================
    @staticmethod
    def _doc_bytes_as_pdf(raw):
        """Return PDF bytes for an uploaded document. A PDF passes through;
        an image is wrapped into a single letter-ish page."""
        if raw[:5] == b'%PDF-':
            return raw
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        if img.mode in ('RGBA', 'P', 'LA'):
            img = img.convert('RGB')
        # Cap the long side so a phone photo doesn't become a metre-wide page.
        max_side = 2200
        if max(img.size) > max_side:
            img.thumbnail((max_side, max_side))
        out = io.BytesIO()
        img.save(out, format='PDF', resolution=200.0)
        return out.getvalue()

    def _employment_file_pdf_bytes(self):
        """Merge W-9 + I-9 + uploaded documents into one PDF (in that order)."""
        self.ensure_one()
        try:
            from pypdf import PdfReader, PdfWriter
        except ImportError:  # pragma: no cover
            from PyPDF2 import PdfReader, PdfWriter  # type: ignore

        parts = [self._official_pdf_bytes('w9'),
                 self._official_pdf_bytes('i9')]
        for bin_field in ('x_id_doc', 'x_id_doc_back', 'x_ssn_doc'):
            stored = self[bin_field]
            if not stored:
                continue
            try:
                raw = base64.b64decode(stored)
                parts.append(self._doc_bytes_as_pdf(raw))
            except Exception as e:  # noqa: BLE001 - skip an unreadable doc
                _logger.warning(
                    "Employment file: skipped %s for applicant %s: %s",
                    bin_field, self.id, e)

        writer = PdfWriter()
        for pdf in parts:
            try:
                writer.append(PdfReader(io.BytesIO(pdf)))
            except Exception as e:  # noqa: BLE001
                _logger.warning(
                    "Employment file: could not append a part for "
                    "applicant %s: %s", self.id, e)
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()

    def action_print_employment_file(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': '/elks/applicant/%s/employment_file.pdf' % self.id,
            'target': 'new',
        }

    def _copy_documents_to_employee(self, emp):
        """Attach the applicant's SSN/work-auth doc and photo ID (and the
        filled W-9 / I-9) to the employee as viewable sidebar attachments.

        Reliable by construction: each document is rebuilt from the uploaded
        binary (or its sidebar copy) with a descriptive name, so nothing is
        skipped and nothing unrelated is dragged along.
        """
        self.ensure_one()
        Att = self.env['ir.attachment'].sudo()
        work_label = self._work_doc_label() or "Work-Authorization Document"
        # (binary field, filename field, human label) for each ID document.
        docs = [
            ('x_id_doc', 'x_id_doc_name', 'Photo ID (front)'),
            ('x_id_doc_back', 'x_id_doc_back_name', 'Photo ID (back)'),
            ('x_ssn_doc', 'x_ssn_doc_name', work_label),
        ]
        for bin_field, name_field, label in docs:
            data = self[bin_field]
            fname = self[name_field] or ''
            if not data:
                # Fall back to a sidebar copy uploaded via the website form.
                existing = Att.search([
                    ('res_model', '=', 'hr.applicant'),
                    ('res_id', '=', self.id),
                    ('res_field', '=', False),
                    ('name', 'ilike', label),
                ], limit=1)
                if existing:
                    existing.copy({
                        'res_model': 'hr.employee', 'res_id': emp.id,
                        'res_field': False, 'name': existing.name})
                continue
            Att.create({
                'name': '%s%s' % (label, ' - %s' % fname if fname else ''),
                'datas': data,
                'res_model': 'hr.employee',
                'res_id': emp.id,
                'res_field': False,
            })
        # Also file the completed government forms for the employee folder.
        for kind, label in (('w9', 'W-9'), ('i9', 'I-9')):
            try:
                pdf = self._official_pdf_bytes(kind)
                Att.create({
                    'name': '%s - %s.pdf' % (
                        label, emp.name or self.x_legal_name or 'contractor'),
                    'datas': base64.b64encode(pdf),
                    'res_model': 'hr.employee',
                    'res_id': emp.id,
                    'res_field': False,
                    'mimetype': 'application/pdf',
                })
            except Exception as e:  # noqa: BLE001 - never block conversion
                _logger.warning(
                    "Could not attach filled %s for applicant %s: %s",
                    label, self.id, e)

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
            "%(cls)s, backup withholding: %(bw)s. I-9 work-auth document: "
            "%(doc)s.",
            tt=(self.x_tin_type or 'ssn').upper(),
            cls=wc or 'n/a',
            bw=_("not subject (certified)") if self.x_not_backup_withholding
            else _("not certified"),
            doc=self._work_doc_label() or _("not specified")))
        # Carry the employee's identity documents onto the new employee record
        # as viewable sidebar attachments (res_field=False), so the SSN card /
        # work-auth document and the photo ID travel with them to the employee
        # file. Built straight from the uploaded binaries with clear names, so
        # each is guaranteed to land regardless of how it was stored.
        self._copy_documents_to_employee(emp)
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
