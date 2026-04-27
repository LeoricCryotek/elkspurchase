# -*- coding: utf-8 -*-
"""Extends ``purchase.order.line`` with an FRS GL Account per line item,
budget availability checking, and an "Ordered" flag that creates a
journal entry to move spending from encumbered to actual."""
import datetime
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    x_elks_account_id = fields.Many2one(
        "elks.account", string="GL Account",
        domain="[('account_type', '=', 'expense')]",
        help="FRS account this line item will be charged to.",
    )
    x_budget_line_id = fields.Many2one(
        "elks.budget.line", string="Budget Line",
        compute="_compute_budget_line", store=False,
    )
    x_budget_available = fields.Monetary(
        "Budget Available", compute="_compute_budget_available",
        currency_field='currency_id',
    )
    x_over_budget = fields.Boolean(
        "Over Budget", compute="_compute_budget_available",
    )
    x_ordered = fields.Boolean(
        "Ordered", default=False, copy=False,
        readonly=True,
        help="Check when this item has been ordered / paid. "
             "Creates a journal entry moving spend from encumbered to actual.",
    )
    x_ordered_date = fields.Date(
        "Ordered Date", copy=False, readonly=True,
        help="Date the line was flagged as ordered.",
    )
    x_journal_entry_id = fields.Many2one(
        "elks.journal.entry", string="Journal Entry",
        copy=False, readonly=True,
        help="FRS journal entry created when this line was marked ordered.",
    )

    @api.depends("x_elks_account_id")
    def _compute_budget_line(self):
        """Auto-resolve the budget line from the GL account."""
        today = datetime.date.today()
        fye = datetime.date(
            today.year + 1 if today.month >= 4 else today.year, 3, 31,
        )
        # Prefer approved budgets, but fall back to draft if none approved yet
        budget = self.env['elks.budget'].search([
            ('fiscal_year_end', '=', fye),
            ('state', 'in', ('board_pending', 'board_approved', 'floor_pending', 'floor_approved', 'submitted')),
        ], limit=1)
        if not budget:
            budget = self.env['elks.budget'].search([
                ('fiscal_year_end', '=', fye),
            ], limit=1)
        for line in self:
            if line.x_elks_account_id and budget:
                bl = self.env['elks.budget.line'].search([
                    ('budget_id', '=', budget.id),
                    ('account_id', '=', line.x_elks_account_id.id),
                ], limit=1)
                line.x_budget_line_id = bl.id if bl else False
            else:
                line.x_budget_line_id = False

    @api.depends("x_budget_line_id", "price_subtotal")
    def _compute_budget_available(self):
        """Check how much budget remains for this GL account."""
        for line in self:
            if line.x_budget_line_id:
                bl = line.x_budget_line_id
                # available_amount already accounts for actuals + encumbrances
                if hasattr(bl, 'available_amount'):
                    line.x_budget_available = bl.available_amount
                else:
                    line.x_budget_available = bl.amount - bl.actual_amount
                line.x_over_budget = line.price_subtotal > line.x_budget_available
            else:
                line.x_budget_available = 0.0
                line.x_over_budget = False

    @api.onchange("x_elks_account_id", "price_subtotal")
    def _onchange_check_budget(self):
        """Warn user if this line exceeds the available budget."""
        if self.x_over_budget and self.x_budget_line_id:
            return {
                'warning': {
                    'title': _("Over Budget"),
                    'message': _(
                        "This line item ($%(amount)s) exceeds the available "
                        "budget for %(account)s.\n\n"
                        "Budget available: $%(available)s\n"
                        "Shortfall: $%(shortfall)s\n\n"
                        "You can still submit this requisition, but a budget "
                        "transfer will be needed before approval.",
                        amount=f"{self.price_subtotal:,.2f}",
                        account=self.x_elks_account_id.display_name,
                        available=f"{self.x_budget_available:,.2f}",
                        shortfall=f"{self.price_subtotal - self.x_budget_available:,.2f}",
                    ),
                }
            }

    # ------------------------------------------------------------------
    # Mark as Ordered — creates a posted journal entry
    # ------------------------------------------------------------------
    def action_mark_ordered(self):
        """Flag selected PO lines as ordered and create a posted FRS
        journal entry debiting the expense GL account and crediting
        the Operating Checking account (10100).

        Can be called on multiple lines at once (e.g. from list view
        action). Lines on the same PO are grouped into one entry.
        """
        lines_to_process = self.filtered(
            lambda l: not l.x_ordered
            and l.order_id.x_approval_state == 'approved'
            and l.x_elks_account_id
        )
        if not lines_to_process:
            raise UserError(_(
                "No lines to mark as ordered. Lines must:\n"
                "- Be on an approved PO\n"
                "- Have a GL Account assigned\n"
                "- Not already be marked as ordered"
            ))

        # Find the Operating Checking account (10100)
        Account = self.env['elks.account']
        checking_acct = Account.search([('code', '=', '10100')], limit=1)
        if not checking_acct:
            raise UserError(_(
                "Operating Checking account (10100) not found in the "
                "Chart of Accounts. Please create it first."
            ))

        JournalEntry = self.env['elks.journal.entry']
        today = fields.Date.context_today(self)

        # Group lines by PO so we create one entry per PO
        po_groups = {}
        for line in lines_to_process:
            po_groups.setdefault(line.order_id, self.env['purchase.order.line'])
            po_groups[line.order_id] |= line

        entries = JournalEntry
        for po, po_lines in po_groups.items():
            je_lines = []
            total = 0.0
            for line in po_lines:
                je_lines.append((0, 0, {
                    'account_id': line.x_elks_account_id.id,
                    'debit': line.price_subtotal,
                    'credit': 0.0,
                    'memo': f"{po.name} — {line.name}",
                }))
                total += line.price_subtotal

            # Credit side — Operating Checking
            je_lines.append((0, 0, {
                'account_id': checking_acct.id,
                'debit': 0.0,
                'credit': total,
                'memo': f"Payment for {po.name}",
            }))

            entry = JournalEntry.create({
                'date': today,
                'memo': _(
                    "Purchase Order %(po)s — items ordered",
                    po=po.name,
                ),
                'line_ids': je_lines,
            })
            # Auto-post the entry
            entry.action_post()
            entries |= entry

            # Mark lines as ordered and link the journal entry
            po_lines.write({
                'x_ordered': True,
                'x_ordered_date': today,
                'x_journal_entry_id': entry.id,
            })

            po.message_post(
                body=_(
                    "<b>Items Ordered</b><br/>"
                    "%(count)s line(s) marked as ordered. "
                    "Journal entry %(entry)s posted — $%(total)s "
                    "debited from Operating Checking.",
                    count=len(po_lines),
                    entry=entry.entry_number,
                    total=f"{total:,.2f}",
                ),
                subtype_xmlid='mail.mt_comment',
            )

        # Return the journal entry if only one, otherwise show list
        if len(entries) == 1:
            return {
                'type': 'ir.actions.act_window',
                'name': _("Journal Entry"),
                'res_model': 'elks.journal.entry',
                'res_id': entries[0].id,
                'view_mode': 'form',
            }
        if len(entries) > 1:
            return {
                'type': 'ir.actions.act_window',
                'name': _("Journal Entries"),
                'res_model': 'elks.journal.entry',
                'domain': [('id', 'in', entries.ids)],
                'view_mode': 'list,form',
            }

    def action_unmark_ordered(self):
        """Reverse the ordered flag and cancel the linked journal entry."""
        for line in self:
            if not line.x_ordered:
                continue
            if line.x_journal_entry_id:
                if line.x_journal_entry_id.state == 'posted':
                    line.x_journal_entry_id.action_cancel()
                line.order_id.message_post(
                    body=_(
                        "<b>Order Reversed</b><br/>"
                        "Line '%(product)s' unmarked as ordered. "
                        "Journal entry %(entry)s cancelled.",
                        product=line.name,
                        entry=line.x_journal_entry_id.entry_number,
                    ),
                    subtype_xmlid='mail.mt_comment',
                )
            line.write({
                'x_ordered': False,
                'x_ordered_date': False,
                'x_journal_entry_id': False,
            })
