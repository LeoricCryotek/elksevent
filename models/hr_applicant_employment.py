# -*- coding: utf-8 -*-
"""Repeating sections of the Lodge Employment Application (W-2 hire).

Education history, professional references, and prior employers hang off the
recruitment applicant so the whole packet can be printed for the office.
"""
from odoo import fields, models


class ElksJobEducation(models.Model):
    _name = "elks.job.education"
    _description = "Employment Application - Education"
    _order = "sequence, id"

    applicant_id = fields.Many2one(
        "hr.applicant", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    level = fields.Selection([
        ("hs", "High School"),
        ("college", "College"),
        ("other", "Other"),
    ], string="Level", default="hs")
    school = fields.Char("School")
    address = fields.Char("Address")
    date_from = fields.Char("From")
    date_to = fields.Char("To")
    graduated = fields.Boolean("Graduated")
    degree = fields.Char("Degree / Diploma")


class ElksJobReference(models.Model):
    _name = "elks.job.reference"
    _description = "Employment Application - Reference"
    _order = "sequence, id"

    applicant_id = fields.Many2one(
        "hr.applicant", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char("Full Name")
    relationship = fields.Char("Relationship")
    company = fields.Char("Company")
    phone = fields.Char("Phone")
    address = fields.Char("Address")


class ElksJobPriorEmployer(models.Model):
    _name = "elks.job.prior.employer"
    _description = "Employment Application - Prior Employer"
    _order = "sequence, id"

    applicant_id = fields.Many2one(
        "hr.applicant", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    company = fields.Char("Company")
    phone = fields.Char("Phone")
    address = fields.Char("Address")
    supervisor = fields.Char("Supervisor")
    title = fields.Char("Job Title")
    salary_start = fields.Char("Starting Salary")
    salary_end = fields.Char("Ending Salary")
    responsibilities = fields.Char("Responsibilities")
    date_from = fields.Char("From")
    date_to = fields.Char("To")
    reason_leaving = fields.Char("Reason for Leaving")
    may_contact = fields.Boolean("May Contact Supervisor")
