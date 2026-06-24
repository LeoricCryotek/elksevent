# -*- coding: utf-8 -*-
"""Map the old single board-status field onto the new approval state.

Runs before the schema update drops ``x_board_status``. We pre-create the
new ``x_approval_state`` column and populate it from the old values so any
in-flight events keep their place in the workflow after upgrade.

    not_submitted -> draft
    submitted     -> board
    approved      -> approved
    denied        -> rejected
"""


def migrate(cr, version):
    # Only act if the old column is still present (i.e. a real upgrade).
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'project_task' AND column_name = 'x_board_status'
    """)
    if not cr.fetchone():
        return

    cr.execute("""
        ALTER TABLE project_task
        ADD COLUMN IF NOT EXISTS x_approval_state varchar
    """)
    cr.execute("""
        UPDATE project_task
        SET x_approval_state = CASE x_board_status
            WHEN 'submitted' THEN 'board'
            WHEN 'approved'  THEN 'approved'
            WHEN 'denied'    THEN 'rejected'
            ELSE 'draft'
        END
        WHERE x_board_status IS NOT NULL
          AND (x_approval_state IS NULL OR x_approval_state = 'draft')
    """)
