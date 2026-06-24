# -*- coding: utf-8 -*-
"""Event checklist template — drives auto-creation of staff subtasks.

HUMAN
-----
This is the editable version of the lodge's paper "Check List for Events".
Each line is one item (Tables, Linen, Bartenders, Insurance, …) with the
subtask title staff should see, who it's assigned to, and how long it takes.
When an event is approved (or when staff click the button), the items that
actually apply to that event turn into sub-tasks for staff to tick off. Edit
the template here — no code change needed.

AI
--
- Two models: `elks.event.checklist.template` (header, `is_default`,
  one2many `line_ids`) and `elks.event.checklist.template.line`.
- `get_default_template()` -> the is_default active template, else first active.
- Consumed by project.task._generate_checklist_subtasks(); applicability per
  item_code is decided in project.task._checklist_applicability().
- `CHECKLIST_ITEM_CODES` is the single source of truth for codes; the seed
  template lives in data/event_checklist_data.xml (noupdate).
"""
from odoo import api, fields, models


# ──────────────────────────────────────────────────────────────────────
# SECTION: Canonical item codes
# HUMAN: The master list of 20 checklist items and their labels.
# AI: Keys MUST stay in sync with project_task._checklist_applicability().
# ──────────────────────────────────────────────────────────────────────
CHECKLIST_ITEM_CODES = [
    ('tables', 'Tables'),
    ('chairs', 'Chairs'),
    ('podium', 'Podium'),
    ('sound', 'Sound System'),
    ('microphone', 'Microphone'),
    ('linen', 'Linen'),
    ('insurance', 'Insurance'),
    ('kitchen', 'Use of Kitchen'),
    ('garbage', 'Garbage'),
    ('bars', 'Bars'),
    ('beer_tub', 'Beer Tub'),
    ('decorations', 'Decorations'),
    ('decor_takedown', 'Takedown Decorations'),
    ('setup', 'Setup'),
    ('takedown', 'Takedown'),
    ('cleanup', 'Clean Up'),
    ('food_us', 'Food by Us'),
    ('food_catered', 'Food Catered By'),
    ('rooms', 'Rooms'),
    ('extras', 'Extras Supplied'),
]


# ──────────────────────────────────────────────────────────────────────
# SECTION: Checklist template (header + lines)
# HUMAN: A named, reusable checklist; mark one as the Default.
# AI: get_default_template() is the lookup used at generation time.
# ──────────────────────────────────────────────────────────────────────
class EventChecklistTemplate(models.Model):
    _name = "elks.event.checklist.template"
    _description = "Event Checklist Template"
    _order = "name"

    name = fields.Char(required=True, default="Standard Event Checklist")
    active = fields.Boolean(default=True)
    is_default = fields.Boolean(
        "Default",
        help="The template used to generate subtasks for new events when no "
             "other is specified.",
    )
    line_ids = fields.One2many(
        "elks.event.checklist.template.line", "template_id",
        string="Checklist Items",
    )

    @api.model
    def get_default_template(self):
        """Return the default active template (or the first active one)."""
        return self.search(
            [('is_default', '=', True), ('active', '=', True)], limit=1,
        ) or self.search([('active', '=', True)], limit=1)


class EventChecklistTemplateLine(models.Model):
    _name = "elks.event.checklist.template.line"
    _description = "Event Checklist Template Line"
    _order = "sequence, id"

    template_id = fields.Many2one(
        "elks.event.checklist.template", required=True, ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    item_code = fields.Selection(
        CHECKLIST_ITEM_CODES, string="Checklist Item", required=True,
    )
    name = fields.Char(
        "Subtask Title",
        help="Title for the generated subtask. Defaults to the item label.",
    )
    create_subtask = fields.Boolean("Create Subtask", default=True)
    default_user_id = fields.Many2one(
        "res.users", string="Assign To",
        help="Default assignee for the generated subtask.",
    )
    planned_hours = fields.Float(
        "Planned Hours",
        help="Optional time allocation for the generated subtask.",
    )
