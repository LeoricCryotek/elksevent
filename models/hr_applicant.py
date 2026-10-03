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

    # ==================================================================
    # W-2 Employment Application (full lodge packet)
    # ==================================================================
    x_is_employment_app = fields.Boolean(
        "Employment Application",
        help="Came in through the full 'Apply for Employment' packet form.")
    x_position_applied = fields.Char("Position Applied For")
    x_date_available = fields.Date("Date Available")
    x_desired_salary = fields.Char("Desired Salary")
    x_is_citizen = fields.Boolean("U.S. Citizen")
    x_worked_here_before = fields.Boolean("Worked Here Before")
    x_worked_here_when = fields.Char("If so, when")
    x_felony = fields.Boolean("Convicted of a Felony")
    x_felony_explain = fields.Char("Felony Explanation")
    # Military service
    x_mil_branch = fields.Char("Military Branch")
    x_mil_from = fields.Char("Military From")
    x_mil_to = fields.Char("Military To")
    x_mil_rank = fields.Char("Rank at Discharge")
    x_mil_discharge = fields.Char("Type of Discharge")
    x_mil_explain = fields.Char("Discharge Explanation (if not honorable)")
    # Repeating sections
    x_education_ids = fields.One2many(
        "elks.job.education", "applicant_id", string="Education")
    x_reference_ids = fields.One2many(
        "elks.job.reference", "applicant_id", string="References")
    x_prior_employer_ids = fields.One2many(
        "elks.job.prior.employer", "applicant_id", string="Prior Employers")
    # W-4 (federal withholding)
    x_w4_filing = fields.Selection([
        ('single', 'Single or Married filing separately'),
        ('married', 'Married filing jointly or Qualifying surviving spouse'),
        ('hoh', 'Head of Household'),
    ], string="W-4 Filing Status")
    x_w4_multiple_jobs = fields.Boolean("W-4 Step 2 (multiple jobs) applies")
    x_w4_dependents_amt = fields.Char("W-4 Dependents Amount (Step 3)")
    x_w4_other_income = fields.Char("W-4 Other Income (Step 4a)")
    x_w4_deductions = fields.Char("W-4 Deductions (Step 4b)")
    x_w4_extra_withholding = fields.Char("W-4 Extra Withholding (Step 4c)")
    # Idaho W-4 (state withholding)
    x_id_w4_status = fields.Selection([
        ('A', 'A — Single'),
        ('B', 'B — Married'),
        ('C', 'C — Married, but withhold at Single rate'),
    ], string="Idaho W-4 Withholding Status")
    x_id_w4_allowances = fields.Char("Idaho W-4 Allowances (line 1)")
    x_id_w4_additional = fields.Char("Idaho W-4 Additional Withholding (line 2)")
    # Direct deposit
    x_dd_request_type = fields.Selection([
        ('new', 'New enrollment'),
        ('change', 'Change existing account(s)'),
        ('cancel', 'Cancel direct deposit'),
    ], string="Direct Deposit Request", default='new')
    x_dd_bank_name = fields.Char("Bank / Credit Union")
    x_dd_bank_address = fields.Char("Bank Address")
    x_dd_account_type = fields.Selection([
        ('checking', 'Checking'), ('savings', 'Savings')],
        string="Account Type")
    x_dd_routing = fields.Char("Routing Number")
    x_dd_account = fields.Char("Account Number")
    x_dd_authorized = fields.Boolean("Direct Deposit Authorized")
    # Employee Acknowledgement (Harassment Policy + Confidentiality Agreement)
    x_ack_signed = fields.Boolean(
        "Employee Acknowledgement Signed",
        help="Acknowledged and agreed to the Harassment Policy and "
             "Confidentiality Agreement (packet page 8).")

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
    x_signature_datetime = fields.Datetime(
        "Electronically Signed At",
        help="Timestamp of the applicant's electronic signature (when they "
             "submitted the online application). Printed under each signature "
             "as the DocuSign-style e-signature certification.")

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

    def _i9_field_values(self, include_sign=True):
        """Map applicant data onto the official Form I-9 (Section 1).

        include_sign=False leaves the signature / date blank so the caller can
        overlay a cursive electronic signature in those fields instead.
        """
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
        }
        if include_sign:
            text['Signature of Employee'] = self.x_signature_name or ''
            text["Today's Date mmddyyy"] = self._mmddyyyy(
                self.x_signature_date or fields.Date.context_today(self))
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

    def _official_pdf_bytes(self, kind, i9_include_sign=True):
        self.ensure_one()
        if kind == 'w9':
            text, checks = self._w9_field_values()
            return self._fill_pdf('w9_blank.pdf', text, checks)
        text, checks = self._i9_field_values(include_sign=i9_include_sign)
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
    # SECTION: Lodge Application Packet — stamp the EXACT lodge forms
    # HUMAN: "Print Lodge Packet" lays the applicant's answers onto the real
    #        9-page lodge packet (Employment Application, I-9, federal W-4,
    #        Idaho W-4, Direct Deposit, Employee Acknowledgement / Harassment
    #        & Confidentiality signature page) and the uploaded ID / SSN card.
    # AI: The template (static/src/pdf/lodge_packet.pdf) is a FLATTENED copy of
    #     the lodge packet, so a reportlab overlay merged with pypdf lands on
    #     top. Page 3 (I-9 Section 1) is replaced with the tested official-I-9
    #     fill. Coordinates are PDF points, origin TOP-LEFT (converted to
    #     reportlab's bottom-left at draw time). Tweak a number here if a field
    #     needs nudging; nothing else depends on these positions.
    # ==================================================================
    _CURSIVE_FONT = 'Times-Italic'  # fallback if the Chancery font is missing

    def _register_cursive_font(self):
        """Register the bundled URW Chancery (Zapf Chancery) Type1 font for the
        cursive electronic signature; fall back to Times-Italic on any error.
        Returns the usable font name."""
        from reportlab.pdfbase import pdfmetrics
        name = 'ElksSignature'
        if name in pdfmetrics.getRegisteredFontNames():
            return name
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        fdir = os.path.join(base, 'static', 'src', 'fonts')
        afm = os.path.join(fdir, 'URWChanceryL-MediItal.afm')
        pfb = os.path.join(fdir, 'URWChanceryL-MediItal.pfb')
        try:
            face = pdfmetrics.EmbeddedType1Face(afm, pfb)
            pdfmetrics.registerTypeFace(face)
            pdfmetrics.registerFont(
                pdfmetrics.Font(name, face.name, 'WinAnsiEncoding'))
            return name
        except Exception:  # noqa: BLE001 - fall back to a built-in italic
            return self._CURSIVE_FONT

    def _esign_stamp(self):
        """DocuSign-style certification line: when the applicant e-signed."""
        self.ensure_one()
        dt = self.x_signature_datetime
        if dt:
            local = fields.Datetime.context_timestamp(self, dt)
            return "Electronically signed %s" % local.strftime(
                '%m/%d/%Y %I:%M %p')
        d = self.x_signature_date or fields.Date.context_today(self)
        return "Electronically signed %s" % d.strftime('%m/%d/%Y')

    def _lodge_packet_overlays(self):
        """Build {page_index: {'text':[...], 'check':[...], 'sig':[...]}}
        with y measured from the TOP of the page (converted at draw time).
        'sig' entries draw the cursive electronic signature + timestamp."""
        self.ensure_one()
        f = self.x_first_name or ''
        mi = (self.x_middle_initial or '')[:1]
        last = self.x_last_name or ''
        full = self.x_legal_name or (f + ' ' + last).strip()
        date = self._mmddyyyy(
            self.x_signature_date or fields.Date.context_today(self))
        dob = self._mmddyyyy(self.x_dob)
        ssn = self.x_ssn or ''
        ssn_digits = ''.join(c for c in ssn if c.isdigit())[:9]
        phone = self.partner_phone or ''
        email = self.email_from or ''
        csz = ', '.join(p for p in [
            self.x_addr_city or '',
            ' '.join(x for x in [self.x_addr_state or '',
                                 self.x_addr_zip or ''] if x)] if p)
        sig_name = self.x_signature_name or full
        ov = {i: {'text': [], 'check': [], 'sig': []} for i in range(9)}

        def T(pg, x, y, val, size=9):
            if val not in (None, '', False):
                ov[pg]['text'].append((x, y, str(val), size))

        def X(pg, x, y, size=11):
            ov[pg]['check'].append((x, y, size))

        def S(pg, x, y, size=15):
            # Cursive signature name with the e-sign certification beneath it.
            if sig_name:
                ov[pg]['sig'].append((x, y, sig_name, size))

        # -------- PAGE 1: Employment Application --------
        T(0, 100, 116, last); T(0, 290, 116, f); T(0, 410, 116, mi)
        T(0, 480, 117, date)
        T(0, 90, 139, self.x_addr_street); T(0, 340, 139, self.x_addr_apt)
        T(0, 90, 161, self.x_addr_city); T(0, 285, 161, self.x_addr_state)
        T(0, 395, 161, self.x_addr_zip)
        T(0, 95, 186, phone); T(0, 300, 186, email)
        T(0, 95, 209, self._mmddyyyy(self.x_date_available))
        T(0, 270, 209, ssn); T(0, 440, 209, self.x_desired_salary)
        T(0, 115, 232, self.x_position_applied)
        X(0, 239, 252) if self.x_is_citizen else X(0, 260, 252)
        if not self.x_is_citizen and self.x_work_auth:
            X(0, 536, 252)
        X(0, 260, 271) if not self.x_worked_here_before else X(0, 239, 271)
        X(0, 260, 290) if not self.x_felony else X(0, 239, 290)
        T(0, 330, 273, self.x_worked_here_when)
        T(0, 95, 312, self.x_felony_explain)
        edu = {e.level: e for e in self.x_education_ids}
        rows = [('hs', 354, 374), ('college', 393, 413), ('other', 432, 451)]
        for lvl, ny, fy in rows:
            e = edu.get(lvl)
            if not e:
                continue
            T(0, 90, ny, e.school); T(0, 292, ny, e.address)
            T(0, 58, fy, e.date_from); T(0, 138, fy, e.date_to)
            T(0, 350, fy, e.degree)
            if e.graduated:
                X(0, 279, fy - 1, 10)
        refs = list(self.x_reference_ids)[:3]
        ry = [(508, 527, 546), (565, 584, 603), (623, 644, 661)]
        for i, r in enumerate(refs):
            fy, cy, ay = ry[i]
            T(0, 95, fy, r.name); T(0, 312, fy, r.relationship)
            T(0, 95, cy, r.company); T(0, 385, cy, r.phone)
            T(0, 95, ay, r.address)

        # -------- PAGE 2: Previous Employment / Military / Signature --------
        pe = list(self.x_prior_employer_ids)[:3]
        # Each prior-employer block: (company, address, jobtitle, responsib.,
        # from/to, may-contact) y-rows. Three blocks down the page.
        pey = [(112, 135, 157, 180, 203, 158),
               (236, 259, 281, 304, 327, 282),
               (360, 383, 405, 428, 451, 406)]
        for i, p in enumerate(pe):
            cy, ay, jy, ry2, fy2, qy = pey[i]
            T(1, 95, cy, p.company); T(1, 390, cy, p.phone)
            T(1, 95, ay, p.address); T(1, 390, ay, p.supervisor)
            T(1, 95, jy, p.title); T(1, 320, jy, p.salary_start)
            T(1, 470, jy, p.salary_end)
            T(1, 130, ry2, p.responsibilities)
            T(1, 60, fy2, p.date_from); T(1, 150, fy2, p.date_to)
            T(1, 320, fy2, p.reason_leaving)
            if p.may_contact:
                X(1, 312, qy)
            else:
                X(1, 333, qy)
        T(1, 90, 592, self.x_mil_branch); T(1, 350, 592, self.x_mil_from)
        T(1, 430, 592, self.x_mil_to)
        T(1, 130, 615, self.x_mil_rank); T(1, 360, 615, self.x_mil_discharge)
        T(1, 95, 638, self.x_mil_explain)
        S(1, 95, 561)
        T(1, 350, 567, date)

        # -------- PAGE 3: replaced by the filled official I-9 (see builder) --

        # -------- PAGE 4: Federal W-4 --------
        T(3, 112, 100, (f + ' ' + mi).strip()); T(3, 278, 100, last)
        T(3, 98, 124, self.x_addr_street); T(3, 98, 148, csz)
        T(3, 497, 100, ssn)
        fsy = {'single': 166, 'married': 178, 'hoh': 190}
        if self.x_w4_filing in fsy:
            X(3, 117, fsy[self.x_w4_filing])
        if self.x_w4_multiple_jobs:
            X(3, 523, 392)
        T(3, 560, 516, self.x_w4_dependents_amt)
        T(3, 560, 560, self.x_w4_other_income)
        T(3, 230, 435, self.x_w4_deductions)
        T(3, 560, 614, self.x_w4_extra_withholding)
        S(3, 115, 698); T(3, 430, 700, date)

        # -------- PAGE 5: Idaho W-4 --------
        idx = {'A': 76, 'B': 158, 'C': 236}
        if self.x_id_w4_status in idx:
            X(4, idx[self.x_id_w4_status], 518)
        T(4, 545, 535, self.x_id_w4_allowances or '0')
        T(4, 545, 556, self.x_id_w4_additional)
        T(4, 42, 594, (f + ' ' + mi).strip()); T(4, 266, 594, last)
        T(4, 408, 593, ssn)
        T(4, 42, 621, self.x_addr_street)
        T(4, 60, 677, self.x_addr_city); T(4, 320, 677, self.x_addr_state)
        T(4, 455, 677, self.x_addr_zip)
        S(4, 100, 723); T(4, 440, 723, date)

        # -------- PAGE 7: Direct Deposit --------
        ddx = {'new': 55, 'change': 175, 'cancel': 335}
        if self.x_dd_request_type in ddx:
            X(6, ddx[self.x_dd_request_type], 116)
        T(6, 113, 164, full); T(6, 456, 164, ssn)
        T(6, 136, 188, self.x_addr_street)
        T(6, 146, 212, csz); T(6, 386, 212, phone)
        T(6, 98, 236, email)
        T(6, 188, 286, self.x_dd_bank_name)
        T(6, 188, 308, self.x_dd_bank_address)
        T(6, 188, 330, self.x_dd_routing); T(6, 146, 354, self.x_dd_account)
        if self.x_dd_account_type == 'checking':
            X(6, 402, 331)
        elif self.x_dd_account_type == 'savings':
            X(6, 482, 331)
        S(6, 205, 578)
        T(6, 428, 583, date)

        # -------- PAGE 8: Employee Acknowledgement --------
        T(7, 300, 503, full)
        S(7, 305, 543)
        T(7, 140, 596, date)
        return ov

    def _lodge_packet_pdf_bytes(self):
        """Stamp the applicant's data onto the lodge packet and return bytes."""
        self.ensure_one()
        import io as _io
        from reportlab.pdfgen import canvas
        from reportlab.lib.utils import ImageReader
        try:
            from pypdf import PdfReader, PdfWriter
        except ImportError:  # pragma: no cover
            from PyPDF2 import PdfReader, PdfWriter  # type: ignore

        template = self._pdf_template_path('lodge_packet.pdf')
        reader = PdfReader(template)
        ov = self._lodge_packet_overlays()
        cursive = self._register_cursive_font()
        esign = self._esign_stamp()
        sig_name = self.x_signature_name or self.x_legal_name or ''
        date = self._mmddyyyy(
            self.x_signature_date or fields.Date.context_today(self))
        writer = PdfWriter()

        def _draw_sig(c, x, y_base, name, size):
            """Cursive signature sitting on the line, with the e-sign
            certification inline to the right (so it never drops into the
            caption printed just below a tight government signature box).
            y_base is the baseline in reportlab bottom-left coords."""
            c.setFont(cursive, size)
            c.drawString(x, y_base, name)
            try:
                w = c.stringWidth(name, cursive, size)
            except Exception:  # noqa: BLE001
                w = len(name) * size * 0.5
            c.setFont('Helvetica', 6.5)
            c.setFillGray(0.4)
            c.drawString(x + w + 10, y_base + 1, esign)
            c.setFillGray(0)

        # Page 3 (index 2) = the filled official I-9 Section 1, with the
        # signature left blank so we overlay a cursive e-signature on it.
        try:
            i9_page = PdfReader(_io.BytesIO(
                self._official_pdf_bytes('i9', i9_include_sign=False))).pages[0]
            if sig_name:
                iw = float(i9_page.mediabox.width)
                ih = float(i9_page.mediabox.height)
                ibuf = _io.BytesIO()
                ic = canvas.Canvas(ibuf, pagesize=(iw, ih))
                # Official I-9 fields (bottom-left coords): signature + date.
                _draw_sig(ic, 48, 423, sig_name, 14)
                ic.setFont('Helvetica', 9)
                ic.drawString(378, 423, date)
                ic.save(); ibuf.seek(0)
                i9_page.merge_page(PdfReader(ibuf).pages[0])
        except Exception:  # noqa: BLE001 - fall back to the template page
            i9_page = None

        # Images for the ID page (page 9, index 8): front + SSN card on the
        # page; the ID back is appended after.
        def _img_reader(bin_field):
            stored = self[bin_field]
            if not stored:
                return None
            try:
                raw = base64.b64decode(stored)
                if raw[:5] == b'%PDF-':
                    return None  # a PDF upload is appended, not drawn
                from PIL import Image
                im = Image.open(_io.BytesIO(raw))
                if im.mode in ('RGBA', 'P', 'LA'):
                    im = im.convert('RGB')
                return ImageReader(im)
            except Exception:  # noqa: BLE001
                return None

        id_front = _img_reader('x_id_doc')
        ssn_img = _img_reader('x_ssn_doc')

        for i, page in enumerate(reader.pages):
            if i == 2 and i9_page is not None:
                writer.add_page(i9_page)
                continue
            W = float(page.mediabox.width)
            H = float(page.mediabox.height)
            spec = ov.get(i, {'text': [], 'check': [], 'sig': []})
            draw_img = (i == 8 and (id_front or ssn_img))
            if spec['text'] or spec['check'] or spec.get('sig') or draw_img:
                buf = _io.BytesIO()
                c = canvas.Canvas(buf, pagesize=(W, H))
                if draw_img:
                    # Driver's License (top half), SSN card (bottom half).
                    if id_front:
                        c.drawImage(id_front, 90, H / 2 + 20, width=W - 180,
                                    height=H / 2 - 150,
                                    preserveAspectRatio=True, anchor='n',
                                    mask='auto')
                    if ssn_img:
                        c.drawImage(ssn_img, 90, 80, width=W - 180,
                                    height=H / 2 - 160,
                                    preserveAspectRatio=True, anchor='n',
                                    mask='auto')
                for (x, y, val, size) in spec['text']:
                    c.setFont('Helvetica', size)
                    c.drawString(x, H - y, val)
                for (x, y, size) in spec['check']:
                    c.setFont('Helvetica-Bold', size)
                    c.drawString(x, H - y, 'X')
                for (x, y, nm, size) in spec.get('sig', []):
                    _draw_sig(c, x, H - y, nm, size)
                c.save()
                buf.seek(0)
                try:
                    page.merge_page(PdfReader(buf).pages[0])
                except Exception:  # noqa: BLE001
                    pass
            writer.add_page(page)

        # Append EVERY uploaded document the applicant provided (ID front/back,
        # SSN card, voided check, etc.) so the downloaded packet always carries
        # their attachments. De-dup by content hash against what is already
        # embedded on the ID page (front + SSN card) so nothing doubles up.
        import hashlib

        def _sha(raw):
            return hashlib.sha1(raw).hexdigest()

        seen = set()
        for bf in ('x_id_doc', 'x_ssn_doc'):  # already shown on page 9
            val = self[bf]
            if val:
                try:
                    seen.add(_sha(base64.b64decode(val)))
                except Exception:  # noqa: BLE001
                    pass

        def _append_doc(raw):
            h = _sha(raw)
            if h in seen:
                return
            seen.add(h)
            try:
                writer.append(PdfReader(_io.BytesIO(
                    self._doc_bytes_as_pdf(raw))))
            except Exception:  # noqa: BLE001
                pass

        # ID back from its binary field first (keeps a predictable order).
        if self.x_id_doc_back:
            try:
                _append_doc(base64.b64decode(self.x_id_doc_back))
            except Exception:  # noqa: BLE001
                pass
        # Then any other document attachments on the applicant (covers the
        # voided check and applications whose uploads only landed in the
        # sidebar, not the binary fields).
        atts = self.env['ir.attachment'].sudo().search([
            ('res_model', '=', 'hr.applicant'),
            ('res_id', '=', self.id),
            ('res_field', '=', False),
        ])
        for att in atts:
            mt = att.mimetype or ''
            if not (mt.startswith('image/') or mt == 'application/pdf'):
                continue
            try:
                raw = att.raw if att.raw else base64.b64decode(att.datas)
            except Exception:  # noqa: BLE001
                continue
            if raw:
                _append_doc(raw)

        out = _io.BytesIO()
        writer.write(out)
        return out.getvalue()

    def action_print_lodge_packet(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': '/elks/applicant/%s/lodge_packet.pdf' % self.id,
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

    def action_print_employment_packet(self):
        self.ensure_one()
        return self.env.ref(
            'elksevent.action_report_employment_packet').report_action(self)

    def _copy_application_to_employee(self, emp):
        """Put the whole application on the employee file so managers/admins
        have the documents and data: copy every uploaded attachment (photo ID,
        voided check, ...) and attach the complete filled Application Packet
        PDF (application + I-9 + W-4 + Direct Deposit) for signing day one."""
        self.ensure_one()
        Att = self.env['ir.attachment'].sudo()
        for att in Att.search([
                ('res_model', '=', 'hr.applicant'), ('res_id', '=', self.id),
                ('res_field', '=', False)]):
            att.copy({'res_model': 'hr.employee', 'res_id': emp.id,
                      'res_field': False, 'name': att.name})
        try:
            pdf, _dummy = self.env['ir.actions.report'].sudo()._render_qweb_pdf(
                'elksevent.report_employment_packet', res_ids=[self.id])
            Att.create({
                'name': 'Application Packet - %s.pdf' % (emp.name or 'employee'),
                'datas': base64.b64encode(pdf),
                'res_model': 'hr.employee', 'res_id': emp.id,
                'res_field': False, 'mimetype': 'application/pdf'})
        except Exception as e:  # noqa: BLE001 - never block conversion
            _logger.warning(
                "Could not attach application packet for applicant %s: %s",
                self.id, e)

    def action_create_employment_employee(self):
        """Create a W-2 hr.employee from a completed employment application."""
        self.ensure_one()
        Employee = self.env['hr.employee']
        name = self.x_legal_name or self.partner_name
        if not name:
            raise UserError(_("Enter the applicant's legal name first."))
        emp = Employee.create({
            'name': name,
            'work_email': self.email_from or False,
            'work_phone': self.partner_phone or False,
            'x_pay_category': 'w2',
        })
        extra = {
            'private_street': self.x_addr_street or False,
            'private_city': self.x_addr_city or False,
            'private_zip': self.x_addr_zip or False,
            'birthday': self.x_dob or False,
            'ssnid': self.x_ssn or False,
        }
        extra = {k: v for k, v in extra.items() if v and k in emp._fields}
        if extra:
            try:
                emp.write(extra)
            except Exception:  # noqa: BLE001
                pass
        self._copy_application_to_employee(emp)
        self.message_post(body=_(
            "Created employee %s from this employment application.", emp.name))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'hr.employee',
            'res_id': emp.id,
            'view_mode': 'form',
            'target': 'current',
        }

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
