# -*- coding: utf-8 -*-
# Part of Elks Lodge Odoo Modules. See LICENSE file for full copyright.
"""Wizard to record the Floor Vote result on a Purchase Order."""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class FloorVoteWizard(models.TransientModel):
    """Record the outcome of a Floor Vote on a purchase request.

    Captures vote result (approved/rejected), motion number, vote counts,
    and notes.  On confirmation, updates the PO's approval state and posts
    the result to the PO's chatter for the audit trail.
    """
    _name = "elkspurchase.floor.vote.wizard"
    _description = "Record Floor Vote on Purchase Request"

    purchase_order_id = fields.Many2one(
        "purchase.order", required=True, readonly=True,
    )
    amount_total = fields.Monetary(
        "Amount", readonly=True,
        currency_field='currency_id',
    )
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )

    vote_result = fields.Selection(
        [('approved', 'Approved'), ('rejected', 'Rejected')],
        string="Vote Result",
        required=True,
    )
    motion_number = fields.Char(
        "Motion Number",
        help="The motion number from the Lodge meeting minutes.",
    )
    vote_for = fields.Integer("Votes For")
    vote_against = fields.Integer("Votes Against")
    vote_date = fields.Date(
        "Meeting Date",
        default=fields.Date.context_today,
        required=True,
    )
    notes = fields.Text(
        "Notes",
        help="Any conditions, amendments, or additional context.",
    )

    def action_record_vote(self):
        """Record the floor vote and advance the PO."""
        self.ensure_one()
        po = self.purchase_order_id
        if not po:
            raise UserError(_("No Purchase Order linked to this vote."))

        vote_notes = []
        if self.motion_number:
            vote_notes.append(f"Motion: {self.motion_number}")
        if self.vote_for or self.vote_against:
            vote_notes.append(
                f"Vote: {self.vote_for} for / {self.vote_against} against"
            )
        vote_notes.append(f"Meeting date: {self.vote_date}")
        if self.notes:
            vote_notes.append(self.notes)
        notes_text = "\n".join(vote_notes)

        if self.vote_result == 'approved':
            po.action_record_floor_approved(notes=notes_text)
        else:
            po.action_record_floor_rejected(notes=notes_text)

        return {'type': 'ir.actions.act_window_close'}
