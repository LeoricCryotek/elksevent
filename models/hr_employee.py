# -*- coding: utf-8 -*-
"""Event department-manager roles, carried on the employee record.

HUMAN
-----
Instead of picking Bar / Kitchen / Custodial managers in Lodge Settings, you
tag the employee: on their HR record tick the department(s) they manage. One
person can manage any or all departments (overlap is fine). Whoever holds a
department receives that department's digital call-sheet link and certifies the
staffing. A coordinator can still override the manager on an individual event.

For the manager to open the portal call-sheet, the employee must have a login
(a Related User on the HR Settings tab). Without a user their partner still
receives the call-out email, but they can't open /my to certify.

AI
--
- Three booleans (x_is_event_bar_mgr / _kitchen_ / _custodial_) so one person
  can hold multiple departments. Replaced the old single Selection
  x_event_dept_role (migrated in migrations/19.0.9.29.0/post-migration.py).
- _event_dept_manager_partner(dept): the login-partner for whoever holds that
  department, else the work-contact partner, else empty. sudo-safe.
"""
from odoo import _, api, fields, models

# department -> the boolean field that flags an employee as its manager
DEPT_MANAGER_FIELD = {
    'bar': 'x_is_event_bar_mgr',
    'kitchen': 'x_is_event_kitchen_mgr',
    'custodial': 'x_is_event_custodial_mgr',
}
DEPT_LABEL = {
    'bar': 'Bar / Lounge Manager',
    'kitchen': 'Kitchen Manager',
    'custodial': 'Custodial Manager',
}


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    x_is_event_bar_mgr = fields.Boolean(
        "Event Bar / Lounge Manager",
        help="This employee manages the bar for events — receives the Bar "
             "call-sheet link and certifies bar staffing. Needs a Related User "
             "(HR Settings tab) to open the portal.")
    x_is_event_kitchen_mgr = fields.Boolean(
        "Event Kitchen Manager",
        help="This employee manages the kitchen for events — receives the "
             "Kitchen call-sheet link and certifies kitchen staffing.")
    x_is_event_custodial_mgr = fields.Boolean(
        "Event Custodial Manager",
        help="This employee manages custodial/cleaning for events — receives "
             "the Custodial call-sheet link and certifies cleaning staffing.")

    @api.onchange('x_is_event_bar_mgr', 'x_is_event_kitchen_mgr',
                  'x_is_event_custodial_mgr')
    def _onchange_event_dept_manager(self):
        """One person may hold several departments (overlap is fine). Only warn
        when a DIFFERENT employee already covers a department this one is taking
        on, since call-outs go to the first holder found."""
        for dept, fld in DEPT_MANAGER_FIELD.items():
            if not self[fld]:
                continue
            dom = [(fld, '=', True)]
            if self._origin.id:
                dom.append(('id', '!=', self._origin.id))
            other = self.sudo().search(dom, limit=1)
            if other:
                return {'warning': {
                    'title': _("Department already has a manager"),
                    'message': _(
                        "%(name)s is already the %(role)s. Call-outs go to the "
                        "first person found, so two people on one department is "
                        "usually a mistake — clear it on %(name)s if it should "
                        "move here.",
                        name=other.name, role=DEPT_LABEL[dept]),
                }}

    @api.model
    def _event_dept_manager_partner(self, dept):
        """Return the res.partner who manages this event department (the first
        employee whose department boolean is set). Prefer their login partner so
        the portal call-sheet link works; fall back to the work contact."""
        fld = DEPT_MANAGER_FIELD.get(dept)
        if not fld:
            return self.env['res.partner']
        emp = self.sudo().search([(fld, '=', True)], limit=1)
        if not emp:
            return self.env['res.partner']
        # Prefer the login partner (portal link works), then the work contact,
        # then any partner matching the employee's work email so simply ticking
        # the box is enough to email the call-out. getattr guards hr variants
        # that lack work_contact_id.
        partner = (emp.user_id.partner_id
                   or getattr(emp, 'work_contact_id', False))
        if not partner and emp.work_email:
            partner = self.env['res.partner'].sudo().search(
                [('email', '=ilike', emp.work_email)], limit=1)
        return partner or self.env['res.partner']
