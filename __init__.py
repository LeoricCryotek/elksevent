# -*- coding: utf-8 -*-
from . import models
from . import controllers
from . import wizard


def alphabetize_app_menus(env):
    """Sort every root app-launcher menu alphabetically by name.
    Mirrors the elkssecretary / elkscontacts Alphabetize App Menus
    tool so new apps automatically fall into place when any Elks
    module is installed or upgraded.  Idempotent — only writes to
    menus whose sequence is already out of position."""
    import logging
    _logger = logging.getLogger(__name__)
    Menu = env['ir.ui.menu'].sudo()
    top_menus = Menu.search([('parent_id', '=', False)])
    sorted_menus = top_menus.sorted(key=lambda m: (m.name or '').lower())
    changed = 0
    for idx, m in enumerate(sorted_menus, start=1):
        new_seq = idx * 10
        if m.sequence != new_seq:
            m.write({'sequence': new_seq})
            changed += 1
    _logger.info(
        "elksevent: alphabetized %d of %d top-level app menus.",
        changed, len(sorted_menus),
    )


def _post_init_hook(env):
    alphabetize_app_menus(env)
