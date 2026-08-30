# -*- coding: utf-8 -*-
"""Carry the old single Event Department Role selection into the new per-
department booleans (one person can now hold several departments).

The old field ``hr.employee.x_event_dept_role`` (Selection bar/kitchen/
custodial) is replaced by three booleans. This copies any previously saved
value into the matching boolean so nothing entered is lost. Guarded so it is a
no-op if the old column never existed on this database.
"""


def _has_column(cr, table, column):
    cr.execute(
        """SELECT 1 FROM information_schema.columns
           WHERE table_name = %s AND column_name = %s""",
        (table, column),
    )
    return bool(cr.fetchone())


def migrate(cr, version):
    if not _has_column(cr, 'hr_employee', 'x_event_dept_role'):
        return
    mapping = {
        'bar': 'x_is_event_bar_mgr',
        'kitchen': 'x_is_event_kitchen_mgr',
        'custodial': 'x_is_event_custodial_mgr',
    }
    for role, col in mapping.items():
        if not _has_column(cr, 'hr_employee', col):
            continue
        # Column name is a fixed literal from the map; value is parameterised.
        cr.execute(
            "UPDATE hr_employee SET %s = TRUE WHERE x_event_dept_role = %%s"
            % col,
            (role,),
        )
