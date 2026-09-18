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
