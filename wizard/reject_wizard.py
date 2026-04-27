# -*- coding: utf-8 -*-
"""Simple wizard to capture a rejection reason before rejecting a requisition."""
from odoo import _, fields, models
from odoo.exceptions import UserError


class RejectRequisitionWizard(models.TransientModel):
    _name = "elks.reject.requisition.wizard"
    _description = "Reject Requisition — Reason"

    purchase_order_id = fields.Many2one(
        "purchase.order", required=True, ondelete="cascade",
    )
    rejection_reason = fields.Text("Reason for Rejection", required=True)
    reject_stage = fields.Selection([
        ('board', 'Board'),
        ('floor', 'Floor'),
    ], required=True)

    def action_confirm_reject(self):
        """Reject the requisition and post the reason to chatter."""
        self.ensure_one()
        order = self.purchase_order_id
        stage_label = "Board" if self.reject_stage == 'board' else "Floor"

        if self.reject_stage == 'board' and order.x_approval_state != 'board':
            raise UserError(_("This requisition is not in the Board queue."))
        if self.reject_stage == 'floor' and order.x_approval_state != 'floor':
            raise UserError(_("This requisition is not in the Floor queue."))

        order.x_approval_state = 'rejected'
        order.message_post(
            body=_(
                "<b>%(stage)s Rejected</b> by %(user)s.<br/>"
                "<b>Reason:</b> %(reason)s",
                stage=stage_label,
                user=self.env.user.name,
                reason=self.rejection_reason,
            ),
            subtype_xmlid='mail.mt_comment',
        )
        return {'type': 'ir.actions.act_window_close'}
