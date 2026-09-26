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
from odoo import _, api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    x_event_count = fields.Integer(
        "Lodge Events", compute="_compute_x_event_count")

    def _compute_x_event_count(self):
        Task = self.env['project.task']
        for partner in self:
            partner.x_event_count = Task.search_count([
                ('x_is_event', '=', True),
                ('partner_id', '=', partner.id),
            ]) if partner.id else 0

    def action_view_lodge_events(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Events with %s", self.name),
            'res_model': 'project.task',
            'view_mode': 'list,form',
            'domain': [('x_is_event', '=', True),
                       ('partner_id', '=', self.id)],
            'context': {'default_partner_id': self.id},
        }

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
