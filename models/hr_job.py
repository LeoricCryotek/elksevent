# -*- coding: utf-8 -*-
"""Let a job posting hide the default website 'Apply' button.

Used for the 1099 posting, which routes applicants to its own onboarding
form (the standard Apply button is confusing there).
"""
from odoo import fields, models


class HrJob(models.Model):
    _inherit = "hr.job"

    x_hide_apply_button = fields.Boolean(
        "Hide Website Apply Button",
        help="Hide the default 'Apply Now' button on this job's website page "
             "(use when the posting links to its own application form).")
