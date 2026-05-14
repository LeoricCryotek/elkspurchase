# -*- coding: utf-8 -*-
"""Extends ``purchase.order`` with Elks Lodge approval workflow.

Simple flow:  Requisition → Board → Floor → Purchase Order
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

ELKS_STATES = [
    ('draft', 'Requisition'),
    ('board', 'Board'),
    ('floor', 'Floor'),
    ('approved', 'Purchase Order'),
    ('rejected', 'Rejected'),
]


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    # ------------------------------------------------------------------
    # Relabel Odoo's built-in state: "RFQ" → "Requisition"
    # ------------------------------------------------------------------
    state = fields.Selection(
        selection_add=[
            ('draft', 'Requisition'),
            ('sent', 'Requisition Sent'),
        ],
    )

    # ------------------------------------------------------------------
    # Requisition title / description
    # ------------------------------------------------------------------
    x_requisition_title = fields.Char(
        "Requisition Title", tracking=True,
        help="Short description of what this purchase is for, "
             "e.g. 'Kitchen cooler replacement' or 'July 4th event supplies'.",
    )

    # ------------------------------------------------------------------
    # Core approval field
    # ------------------------------------------------------------------
    x_approval_state = fields.Selection(
        ELKS_STATES, string="Lodge Status",
        default='draft', tracking=True, copy=False, index=True,
    )

    # Who is asking for the money?
    x_requesting_department_id = fields.Many2one(
        "hr.department", string="Requesting Department", tracking=True,
    )
    x_requesting_committee_id = fields.Many2one(
        "elks.committee", string="Requesting Committee", tracking=True,
    )

    # GL account & budget
    x_elks_account_id = fields.Many2one(
        "elks.account", string="GL Account",
        domain="[('account_type', 'in', ['expense', 'fixed_asset'])]",
        tracking=True,
    )
    x_elks_department_id = fields.Many2one(
        "elks.department", string="Elks Department",
        related="x_elks_account_id.department_id", store=True,
    )
    x_budget_line_id = fields.Many2one(
        "elks.budget.line", string="Budget Line",
        tracking=True, copy=False,
    )
    x_budget_remaining = fields.Monetary(
        "Budget Remaining", compute="_compute_budget_remaining",
        currency_field='currency_id',
    )

    # Maintenance link (x_maintenance_request_id) and sync hook are
    # provided by the ``elksmaintenance_purchase`` bridge module.
    # Keeping them out of here lets elkspurchase install on databases
    # without the ``maintenance`` / ``elksmaintenance`` modules.

    # Over-budget flag (any line over budget)
    x_has_over_budget_lines = fields.Boolean(
        compute="_compute_has_over_budget_lines",
    )

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------
    @api.depends("x_budget_line_id", "x_budget_line_id.amount",
                 "x_budget_line_id.actual_amount", "amount_total")
    def _compute_budget_remaining(self):
        for rec in self:
            if rec.x_budget_line_id:
                bl = rec.x_budget_line_id
                encumbered = sum(self.search([
                    ('x_budget_line_id', '=', bl.id),
                    ('x_approval_state', '=', 'approved'),
                ]).mapped('amount_total'))
                rec.x_budget_remaining = bl.amount - bl.actual_amount - encumbered
            else:
                rec.x_budget_remaining = 0.0

    @api.onchange("x_elks_account_id")
    def _onchange_gl_account(self):
        """Auto-resolve budget line from GL account."""
        if not self.x_elks_account_id:
            self.x_budget_line_id = False
            return
        import datetime
        today = datetime.date.today()
        fye = datetime.date(
            today.year + 1 if today.month >= 4 else today.year, 3, 31,
        )
        budget = self.env['elks.budget'].search([
            ('fiscal_year_end', '=', fye),
            ('state', 'in', ('approved', 'submitted')),
        ], limit=1)
        if budget:
            bl = self.env['elks.budget.line'].search([
                ('budget_id', '=', budget.id),
                ('account_id', '=', self.x_elks_account_id.id),
            ], limit=1)
            self.x_budget_line_id = bl.id if bl else False
        else:
            self.x_budget_line_id = False

    @api.depends("order_line.x_over_budget")
    def _compute_has_over_budget_lines(self):
        for rec in self:
            rec.x_has_over_budget_lines = any(
                l.x_over_budget for l in rec.order_line
            )

    # ------------------------------------------------------------------
    # Budget transfer request
    # ------------------------------------------------------------------
    def action_request_budget_transfer(self):
        """Create draft budget transfer requests for over-budget lines."""
        self.ensure_one()
        over_lines = self.order_line.filtered('x_over_budget')
        if not over_lines:
            raise UserError(_("No line items are over budget."))

        transfers = self.env['elks.budget.transfer']
        for line in over_lines:
            shortfall = line.price_subtotal - line.x_budget_available
            transfer = transfers.create({
                'budget_id': line.x_budget_line_id.budget_id.id,
                'to_line_id': line.x_budget_line_id.id,
                'amount': shortfall,
                'reason': _(
                    "Budget transfer needed for requisition %(po)s.\n"
                    "Line: %(product)s — $%(amount)s\n"
                    "Available: $%(available)s | Shortfall: $%(shortfall)s",
                    po=self.name,
                    product=line.name,
                    amount=f"{line.price_subtotal:,.2f}",
                    available=f"{line.x_budget_available:,.2f}",
                    shortfall=f"{shortfall:,.2f}",
                ),
            })
            transfers |= transfer

        self.message_post(
            body=_(
                "<b>Budget Transfer Request(s) Created</b><br/>"
                "%(count)s transfer(s) pending Secretary approval.",
                count=len(transfers),
            ),
            subtype_xmlid='mail.mt_comment',
        )

        if len(transfers) == 1:
            return {
                'type': 'ir.actions.act_window',
                'name': _("Budget Transfer"),
                'res_model': 'elks.budget.transfer',
                'res_id': transfers[0].id,
                'view_mode': 'form',
            }
        return {
            'type': 'ir.actions.act_window',
            'name': _("Budget Transfers"),
            'res_model': 'elks.budget.transfer',
            'domain': [('id', 'in', transfers.ids)],
            'view_mode': 'list,form',
        }

    # ------------------------------------------------------------------
    # Workflow buttons
    # ------------------------------------------------------------------
    def action_submit(self):
        """Submit requisition to the Board for approval."""
        for order in self:
            if order.x_approval_state != 'draft':
                raise UserError(_("Only draft requisitions can be submitted."))
            if not order.order_line:
                raise UserError(_("Add at least one line item first."))
            order.x_approval_state = 'board'
            order.message_post(
                body=_("Submitted to <b>Board</b> by %s.", self.env.user.name),
                subtype_xmlid='mail.mt_comment',
            )

    def action_board_approve(self):
        """Board approves — advance to Floor vote."""
        for order in self:
            if order.x_approval_state != 'board':
                raise UserError(_("This requisition is not in the Board queue."))
            order.x_approval_state = 'floor'
            order.message_post(
                body=_("<b>Board Approved</b> by %s.", self.env.user.name),
                subtype_xmlid='mail.mt_comment',
            )

    def action_board_reject(self):
        """Open rejection reason wizard for Board rejection."""
        self.ensure_one()
        if self.x_approval_state != 'board':
            raise UserError(_("This requisition is not in the Board queue."))
        return {
            'type': 'ir.actions.act_window',
            'name': _("Reject Requisition"),
            'res_model': 'elks.reject.requisition.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_purchase_order_id': self.id,
                'default_reject_stage': 'board',
            },
        }

    def action_floor_approve(self):
        """Floor approves — confirm as Purchase Order."""
        for order in self:
            if order.x_approval_state != 'floor':
                raise UserError(_("This requisition is not in the Floor queue."))
            order.x_approval_state = 'approved'
            order.message_post(
                body=_("<b>Floor Approved</b> — now a Purchase Order. "
                       "Recorded by %s.", self.env.user.name),
                subtype_xmlid='mail.mt_comment',
            )
        # Run post-approve hooks — bridge modules (e.g.
        # elksmaintenance_purchase) override _elks_post_floor_approve_hooks
        # to add their own side-effects without needing this module to
        # know about them.
        self._elks_post_floor_approve_hooks()
        # Auto-confirm as a real Odoo PO
        return super().button_confirm()

    def _elks_post_floor_approve_hooks(self):
        """Hook for bridge modules to plug in.  No-op base implementation."""
        return

    def action_floor_reject(self):
        """Open rejection reason wizard for Floor rejection."""
        self.ensure_one()
        if self.x_approval_state != 'floor':
            raise UserError(_("This requisition is not in the Floor queue."))
        return {
            'type': 'ir.actions.act_window',
            'name': _("Reject Requisition"),
            'res_model': 'elks.reject.requisition.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_purchase_order_id': self.id,
                'default_reject_stage': 'floor',
            },
        }

    def action_reset_to_draft(self):
        """Reset a rejected requisition back to draft."""
        for order in self:
            if order.x_approval_state != 'rejected':
                raise UserError(_("Only rejected requisitions can be reset."))
            order.x_approval_state = 'draft'
            order.message_post(
                body=_("Reset to <b>Requisition</b> by %s.", self.env.user.name),
                subtype_xmlid='mail.mt_comment',
            )

    # ------------------------------------------------------------------
    # Print — use the full PO report (with prices) instead of the
    # stripped-down quotation template, and don't change state.
    # ------------------------------------------------------------------
    def print_quotation(self):
        """Print the requisition with prices & totals (not the bare RFQ)."""
        # Don't change state to 'sent' — we manage state via x_approval_state
        return self.env.ref(
            'purchase.action_report_purchase_order'
        ).report_action(self)

    # ------------------------------------------------------------------
    # Block standard Odoo confirm unless Floor-approved
    # ------------------------------------------------------------------
    def button_confirm(self):
        for order in self:
            if order.x_approval_state != 'approved':
                raise UserError(_(
                    "This requisition must be approved by the Board and "
                    "Floor before it can become a Purchase Order.\n\n"
                    "Current status: %s",
                    dict(ELKS_STATES).get(order.x_approval_state, '?'),
                ))
        return super().button_confirm()

    # ------------------------------------------------------------------
    # Mark all lines as ordered
    # ------------------------------------------------------------------
    def action_mark_all_ordered(self):
        """Mark all unordered lines on this PO as ordered."""
        self.ensure_one()
        unordered = self.order_line.filtered(
            lambda l: not l.x_ordered and l.x_elks_account_id
        )
        if not unordered:
            raise UserError(_("All line items are already marked as ordered."))
        return unordered.action_mark_ordered()

    # ------------------------------------------------------------------
    # Dashboard — replace standard Odoo KPIs with lodge approval stages
    # ------------------------------------------------------------------
    @api.model
    def retrieve_dashboard(self):
        """Return lodge-specific KPI counts for the purchase dashboard.

        Counts are scoped to the current calendar month so the dashboard
        reflects recent activity rather than all-time totals.
        """
        import datetime
        today = fields.Date.context_today(self)
        month_start = today.replace(day=1)
        month_domain = [('create_date', '>=', month_start)]

        result = {
            'global': {
                'draft': {'all': 0, 'priority': 0},
                'sent': {'all': 0, 'priority': 0},
                'late': {'all': 0, 'priority': 0},
                'not_acknowledged': {'all': 0, 'priority': 0},
                'late_receipt': {'all': 0, 'priority': 0},
                'days_to_order': 0,
            },
            'my': {
                'draft': {'all': 0, 'priority': 0},
                'sent': {'all': 0, 'priority': 0},
                'late': {'all': 0, 'priority': 0},
                'not_acknowledged': {'all': 0, 'priority': 0},
                'late_receipt': {'all': 0, 'priority': 0},
                'days_to_order': 0,
            },
            'days_to_purchase': 0,
            # Lodge-specific keys consumed by our OWL template
            'elks': {},
        }

        PO = self.env['purchase.order']
        for state_key in ('draft', 'board', 'floor', 'approved', 'rejected'):
            count = PO.search_count(
                month_domain + [('x_approval_state', '=', state_key)]
            )
            result['elks'][state_key] = count

        # Total spend this month (approved POs)
        approved_pos = PO.search(
            month_domain + [('x_approval_state', '=', 'approved')]
        )
        result['elks']['approved_total'] = sum(
            approved_pos.mapped('amount_total')
        )

        return result

    # Maintenance sync (_sync_maintenance_po_confirmed) lives in the
    # ``elksmaintenance_purchase`` bridge module — invoked via
    # ``_elks_post_floor_approve_hooks`` so this module doesn't have
    # to know it exists.
