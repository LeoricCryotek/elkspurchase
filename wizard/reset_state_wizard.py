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
        """Reset the requisition's approval state and log the change."""
        self.ensure_one()
        po = self.purchase_order_id
        if self.target_state == po.x_approval_state:
            raise UserError(_(
                "Requisition is already in state '%s' — nothing to reset.",
                dict(APPROVAL_STATES).get(self.target_state),
            ))

        old_label = dict(APPROVAL_STATES).get(po.x_approval_state, po.x_approval_state)
        new_label = dict(APPROVAL_STATES).get(self.target_state, self.target_state)

        po.x_approval_state = self.target_state
        po.message_post(
            body=_(
                "<b>⚠️ Approval state manually reset</b><br/>"
                "%(old)s → %(new)s by %(user)s<br/>"
                "<b>Reason:</b> %(reason)s",
                old=old_label,
                new=new_label,
                user=self.env.user.name,
                reason=self.reason,
            ),
            subtype_xmlid='mail.mt_comment',
        )
        return {'type': 'ir.actions.act_window_close'}
