# -*- coding: utf-8 -*-
"""Extend res.partner with a catering flag.

HUMAN
-----
Business contacts can be flagged "Is Catering" so they show up in the event's
"Catered By" picker. The lodge's own kitchen is one such contact (the default),
and picking it turns on the in-house kitchen call-out.

AI
--
- Adds boolean x_is_catering. The "Catered By" field on project.task domains on
  this flag; the seeded partner elksevent.partner_lodge_kitchen carries it.
"""
from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    x_is_catering = fields.Boolean(
        "Is Catering",
        help="This business is a catering company — it appears in the event "
             "\"Catered By\" picker.")
    x_caterer_insurance_on_file = fields.Boolean(
        "Insurance Certificate on File",
        help="Their certificate of insurance is on file and names Elks Lodge "
             "#896 as additionally insured.")
    x_caterer_insurance_doc = fields.Binary(
        "Insurance Certificate", attachment=True,
        help="Upload the caterer's certificate of insurance naming Elks Lodge "
             "#896 as additionally insured.")
    x_caterer_insurance_doc_name = fields.Char("Insurance Certificate filename")
    x_caterer_insurance_expiry = fields.Date(
        "Insurance Expiration",
        help="When the caterer's certificate of insurance expires.")
