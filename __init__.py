# -*- coding: utf-8 -*-
from . import models
from . import wizard


def _pre_init_migrate_approval_states(env):
    """Migrate old approval states to the simplified two-tier flow."""
    # Rename the column if upgrading from old version
    env.cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'purchase_order'
           AND column_name = 'x_elks_approval_state'
    """)
    if env.cr.fetchone():
        # Rename old column → new column
        env.cr.execute("""
            SELECT 1 FROM information_schema.columns
             WHERE table_name = 'purchase_order'
               AND column_name = 'x_approval_state'
        """)
        if not env.cr.fetchone():
            env.cr.execute("""
                ALTER TABLE purchase_order
                RENAME COLUMN x_elks_approval_state TO x_approval_state
            """)
        # Map old states to new states
        env.cr.execute("""
            UPDATE purchase_order SET x_approval_state = 'board'
             WHERE x_approval_state IN (
                   'committee_review', 'trustee_pending',
                   'trustee_approved', 'board_review')
        """)
        env.cr.execute("""
            UPDATE purchase_order SET x_approval_state = 'approved'
             WHERE x_approval_state IN ('floor_approved', 'purchase')
        """)
        env.cr.execute("""
            UPDATE purchase_order SET x_approval_state = 'floor'
             WHERE x_approval_state = 'floor_pending'
        """)
        env.cr.execute("""
            UPDATE purchase_order SET x_approval_state = 'rejected'
             WHERE x_approval_state = 'floor_rejected'
        """)
