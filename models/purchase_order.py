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
        domain="[('account_type', 'in', ['expense', 'cogs', 'fixed_asset'])]",
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
    # Officer sign-off (post Floor-approval workflow)
    # After a PO reaches x_approval_state='approved', it needs three
    # officer signatures before payment is issued: ER, Treasurer, Secretary.
    # Each row records the signature image, who signed, and when.
    # ------------------------------------------------------------------
    x_er_signature = fields.Binary(
        "ER Signature", copy=False,
        help="Exalted Ruler sign-off. Draw signature or click "
             "'Sign as ER' to stamp with the current user's saved signature.",
    )
    x_er_signed_by_id = fields.Many2one(
        "res.users", string="ER Signed By", copy=False, readonly=True,
    )
    x_er_signed_date = fields.Datetime(
        "ER Signed Date", copy=False, readonly=True,
    )

    x_treasurer_signature = fields.Binary(
        "Treasurer Signature", copy=False,
    )
    x_treasurer_signed_by_id = fields.Many2one(
        "res.users", string="Treasurer Signed By", copy=False, readonly=True,
    )
    x_treasurer_signed_date = fields.Datetime(
        "Treasurer Signed Date", copy=False, readonly=True,
    )

    x_secretary_signature = fields.Binary(
        "Secretary Signature", copy=False,
    )
    x_secretary_signed_by_id = fields.Many2one(
        "res.users", string="Secretary Signed By", copy=False, readonly=True,
    )
    x_secretary_signed_date = fields.Datetime(
        "Secretary Signed Date", copy=False, readonly=True,
    )

    x_all_officers_signed = fields.Boolean(
        "All Officers Signed",
        compute="_compute_all_officers_signed", store=True,
    )

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------
    @api.depends("x_er_signature", "x_treasurer_signature", "x_secretary_signature")
    def _compute_all_officers_signed(self):
        for rec in self:
            rec.x_all_officers_signed = bool(
                rec.x_er_signature
                and rec.x_treasurer_signature
                and rec.x_secretary_signature
            )

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
        """Open a Budget Transfer form pre-populated with the over-budget
        line's destination + shortfall.

        We can't ``.create()`` the elks.budget.transfer directly because the
        model requires ``from_line_id`` — and only the Secretary can decide
        which budget line to pull the money FROM. So we open a fresh form
        with ``default_*`` context values and let them pick the source.

        If there are multiple over-budget lines, we open the form for the
        FIRST one and note the others in the log; user re-clicks the button
        after saving to handle each remaining line.
        """
        self.ensure_one()
        over_lines = self.order_line.filtered('x_over_budget')
        if not over_lines:
            raise UserError(_("No line items are over budget."))

        # Pick the first over-budget line as the destination
        line = over_lines[0]
        shortfall = line.price_subtotal - line.x_budget_available

        remaining = len(over_lines) - 1
        note_extra = ""
        if remaining:
            note_extra = _(
                "\n\n(%(n)s other over-budget line(s) still need transfers — "
                "click Request Budget Transfer again after saving this one.)",
                n=remaining,
            )

        default_reason = _(
            "Budget transfer needed for requisition %(po)s.\n"
            "Line: %(product)s — $%(amount)s\n"
            "Available: $%(available)s | Shortfall: $%(shortfall)s%(extra)s",
            po=self.name,
            product=line.name,
            amount=f"{line.price_subtotal:,.2f}",
            available=f"{line.x_budget_available:,.2f}",
            shortfall=f"{shortfall:,.2f}",
            extra=note_extra,
        )

        self.message_post(
            body=_(
                "<b>Budget Transfer form opened</b><br/>"
                "Line <i>%(product)s</i> is short $%(shortfall)s. "
                "Select a source budget line and save to file the request.",
                product=line.name,
                shortfall=f"{shortfall:,.2f}",
            ),
            subtype_xmlid='mail.mt_comment',
        )

        return {
            'type': 'ir.actions.act_window',
            'name': _("New Budget Transfer"),
            'res_model': 'elks.budget.transfer',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_budget_id': line.x_budget_line_id.budget_id.id,
                'default_to_line_id': line.x_budget_line_id.id,
                'default_amount': shortfall,
                'default_reason': default_reason,
            },
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
    # Officer sign-off actions — stamp the current user's saved
    # signature onto the PO. Each is guarded by the appropriate group
    # via the button's `groups=` attribute in the view, plus a runtime
    # check here (defense in depth against direct RPC calls).
    # ------------------------------------------------------------------
    def _sign_as(self, role):
        """Common helper — stamp signature, user, and timestamp.

        `role` is one of 'er', 'treasurer', 'secretary'. Uses the
        current user's saved signature (res.users.sign_signature) if
        available; otherwise stores a text stamp with the user's name.
        Post-condition: <x_role_signature>, <x_role_signed_by_id>, and
        <x_role_signed_date> are populated and a chatter note is logged.
        """
        self.ensure_one()
        if self.x_approval_state != 'approved':
            raise UserError(_(
                "Officer sign-off is only available on requisitions in "
                "the Approved state (after Floor approval)."
            ))
        role_labels = {
            'er': 'Exalted Ruler',
            'treasurer': 'Treasurer',
            'secretary': 'Secretary',
        }
        role_groups = {
            'er':        'elkspurchase.group_elks_er',
            'treasurer': 'elkspurchase.group_elks_treasurer',
            'secretary': 'elkspurchase.group_elks_secretary',
        }
        if not self.env.user.has_group(role_groups[role]):
            raise UserError(_(
                "You are not authorized to sign as %s. Only members of "
                "the %s group can place this signature.",
                role_labels[role], role_labels[role],
            ))
        sig_field = f'x_{role}_signature'
        user_field = f'x_{role}_signed_by_id'
        date_field = f'x_{role}_signed_date'
        if getattr(self, sig_field):
            raise UserError(_(
                "%s signature is already on this PO. To re-sign, an "
                "admin must first clear the existing signature.",
                role_labels[role],
            ))
        # Prefer the user's saved signature; fall back to a name stamp
        # (rendered as a small SVG so the report can still display something).
        saved_sig = self.env.user.sign_signature if hasattr(self.env.user, 'sign_signature') else None
        signature_value = saved_sig or self._name_stamp_svg(self.env.user.name)
        self.write({
            sig_field: signature_value,
            user_field: self.env.user.id,
            date_field: fields.Datetime.now(),
        })
        self.message_post(
            body=_(
                "<b>%(role)s Signed</b> by %(user)s at %(when)s",
                role=role_labels[role],
                user=self.env.user.name,
                when=fields.Datetime.context_timestamp(
                    self, fields.Datetime.now()).strftime('%Y-%m-%d %I:%M %p'),
            ),
            subtype_xmlid='mail.mt_comment',
        )
        return True

    def action_sign_as_er(self):
        return self._sign_as('er')

    def action_sign_as_treasurer(self):
        return self._sign_as('treasurer')

    def action_sign_as_secretary(self):
        return self._sign_as('secretary')

    @staticmethod
    def _name_stamp_svg(name):
        """Return a base64-encoded SVG that just prints the signer's name
        in a stylized font. Used when the user has no saved signature
        on file."""
        import base64
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="60" '
            f'viewBox="0 0 300 60">'
            f'<text x="10" y="40" font-family="Brush Script MT, cursive" '
            f'font-size="32" fill="#1a1a1a">{name}</text>'
            f'</svg>'
        )
        return base64.b64encode(svg.encode('utf-8'))

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
