# -*- coding: utf-8 -*-
"""Admin wizard to reset a requisition's approval state — used to reverse
an accidental button click (e.g. someone hits Board Approve on the wrong
requisition, or a Floor Approve that should have been rejected).

Reachable from the ⚙ Actions menu on any purchase.order form.
Restricted to purchase.group_purchase_manager.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


APPROVAL_STATES = [
    ('draft',    'Draft (Requisition)'),
    ('board',    'Board (Queued for Board approval)'),
    ('floor',    'Floor (Queued for Floor approval)'),
    ('approved', 'Approved (Purchase Order)'),
    ('rejected', 'Rejected'),
]


class ResetApprovalStateWizard(models.TransientModel):
    _name = "elks.reset.approval.state.wizard"
    _description = "Reset Requisition Approval State"

    purchase_order_id = fields.Many2one(
        "purchase.order", required=True, ondelete="cascade",
    )
    current_state = fields.Selection(
        APPROVAL_STATES, string="Current State", readonly=True,
    )
    target_state = fields.Selection(
        APPROVAL_STATES, string="Reset To", required=True,
    )
    reason = fields.Text(
        "Reason", required=True,
        help="Short explanation logged to chatter (e.g. 'accidental floor "
             "approve — should have been rejected').",
    )
    approved_warning = fields.Boolean(
        compute="_compute_approved_warning",
        help="True when leaving an approved state — user should know that "
             "any journal entries already posted from Mark Ordered are NOT "
             "reversed by this wizard.",
    )

    @api.model
    def default_get(self, fields_list):
        """Pre-fill current_state from the target purchase.order."""
        res = super().default_get(fields_list)
        po_id = self.env.context.get('active_id') or self.env.context.get('default_purchase_order_id')
        if po_id:
            po = self.env['purchase.order'].browse(po_id)
            res['purchase_order_id'] = po.id
            res['current_state'] = po.x_approval_state
        return res

    @api.depends("current_state", "target_state")
    def _compute_approved_warning(self):
        for rec in self:
            rec.approved_warning = (
                rec.current_state == 'approved'
                and rec.target_state != 'approved'
            )

    def action_apply(self):
        """Reset the requisition's approval state and log the change.

        Also syncs the base ``purchase.order.state`` field so downstream
        readonly rules (e.g. partner_id locked when state='purchase') release
        properly.  Without this, resetting only x_approval_state leaves the
        Vendor field greyed-out and unusable.
        """
        self.ensure_one()
        po = self.purchase_order_id
        if self.target_state == po.x_approval_state:
            raise UserError(_(
                "Requisition is already in state '%s' — nothing to reset.",
                dict(APPROVAL_STATES).get(self.target_state),
            ))

        old_label = dict(APPROVAL_STATES).get(po.x_approval_state, po.x_approval_state)
        new_label = dict(APPROVAL_STATES).get(self.target_state, self.target_state)
        old_base_state = po.state

        # Sync the base state so form-field readonly rules relax properly.
        # 'draft'/'board'/'floor'/'rejected' → base state 'draft' (fully editable)
        # 'approved' → base state 'purchase' (matches standard Odoo purchase-approved)
        base_state_map = {
            'draft':    'draft',
            'board':    'draft',
            'floor':    'draft',
            'rejected': 'draft',
            'approved': 'purchase',
        }
        new_base_state = base_state_map.get(self.target_state, 'draft')

        # sudo() so this works even when the current user's ACL is more
        # restrictive on purchase.order.state transitions.
        po.sudo().write({
            'x_approval_state': self.target_state,
            'state': new_base_state,
        })

        po.message_post(
            body=_(
                "<b>⚠️ Approval state manually reset</b><br/>"
                "%(old)s → %(new)s by %(user)s<br/>"
                "(base state: %(old_bs)s → %(new_bs)s — Vendor and line "
                "edits are now unlocked.)<br/>"
                "<b>Reason:</b> %(reason)s",
                old=old_label,
                new=new_label,
                old_bs=old_base_state,
                new_bs=new_base_state,
                user=self.env.user.name,
                reason=self.reason,
            ),
            subtype_xmlid='mail.mt_comment',
        )
        return {'type': 'ir.actions.act_window_close'}
