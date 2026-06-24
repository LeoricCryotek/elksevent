# -*- coding: utf-8 -*-
"""Switch event routing to a single global Events project.

Events now file into one global 'Events' project (Lodge Settings ->
Default Events Project), so the yearly auto-create cron is no longer
needed. Deactivate it on existing databases (the XML data flag only
affects fresh installs because the cron record is noupdate).
"""


def migrate(cr, version):
    cr.execute("""
        UPDATE ir_cron SET active = False
        WHERE id IN (
            SELECT res_id FROM ir_model_data
            WHERE module = 'elksevent'
              AND name = 'cron_create_elk_year_project'
        )
    """)
