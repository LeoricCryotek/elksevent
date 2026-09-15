# -*- coding: utf-8 -*-
"""Carry the old free-text Committee (x_event_committee) into the new
elks.committee dropdown (x_event_committee_id). Matches an existing committee
by name (case-insensitive); creates one if there's no match, so nothing typed
is lost. No-op if the old column was never created."""
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    cr.execute(
        """SELECT 1 FROM information_schema.columns
           WHERE table_name = 'project_task'
             AND column_name = 'x_event_committee'""")
    if not cr.fetchone():
        return
    cr.execute(
        """SELECT id, x_event_committee FROM project_task
           WHERE x_event_committee IS NOT NULL
             AND btrim(x_event_committee) <> ''
             AND x_event_committee_id IS NULL""")
    rows = cr.fetchall()
    if not rows:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Committee = env['elks.committee']
    cache = {}
    for task_id, name in rows:
        key = (name or '').strip().lower()
        if not key:
            continue
        committee = cache.get(key)
        if not committee:
            committee = Committee.search(
                [('name', '=ilike', name.strip())], limit=1)
            if not committee:
                committee = Committee.create({'name': name.strip()})
            cache[key] = committee
        cr.execute(
            "UPDATE project_task SET x_event_committee_id = %s WHERE id = %s",
            (committee.id, task_id))
