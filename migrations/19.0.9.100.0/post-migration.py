# -*- coding: utf-8 -*-
"""Set the 'hide website Apply button' flag on the 1099 posting (noupdate)."""


def migrate(cr, version):
    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name='hr_job' AND column_name='x_hide_apply_button'
    """)
    if not cr.fetchone():
        return
    cr.execute("""
        UPDATE hr_job SET x_hide_apply_button = TRUE
        WHERE id IN (
            SELECT res_id FROM ir_model_data
            WHERE module='elksevent' AND name='job_1099_contractor'
        )
    """)
