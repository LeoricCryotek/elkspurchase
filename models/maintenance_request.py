# -*- coding: utf-8 -*-
# Part of Elks Lodge Odoo Modules. See LICENSE file for full copyright.
"""Extends ``maintenance.request`` with a button to create a PO Request
from the repair cost estimate."""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class MaintenanceRequest(models.Model):
    """Extend maintenance.request with PO creation from repair estimates.

    Adds a 'Create PO Request' button that generates a draft purchase order
    pre-filled from the maintenance request's cost estimate, funding source,
    and GL account.  Tracks linked POs via a smart button.
    """
    _inherit = "maintenance.request"

    x_purchase_order_ids = fields.One2many(
        "purchase.order",
        "x_maintenance_request_id",
        string="Purchase Orders",
    )
    x_purchase_order_count = fields.Integer(
        compute="_compute_po_count",
        string="PO Count",
    )

    @api.depends("x_purchase_order_ids")
    def _compute_po_count(self):
        for rec in self:
            rec.x_purchase_order_count = len(rec.x_purchase_order_ids)

    def action_create_po_request(self):
        """Create a draft Purchase Order pre-filled from this maintenance
        request's cost estimate, funding source, and GL account."""
        self.ensure_one()
        if not self.x_estimated_cost or self.x_estimated_cost <= 0:
            raise UserError(_(
                "Please enter an Estimated Cost on this maintenance request "
                "before creating a PO Request."
            ))

        PO = self.env['purchase.order']
        po_vals = {
            'x_maintenance_request_id': self.id,
            'origin': f"MR-{self.id}: {self.name}",
            'notes': (
                f"Generated from Maintenance Ticket: {self.name}\n"
                f"Issue Type: {self.x_ticket_type or 'N/A'}\n"
                f"Location: {self.x_location_id.name if self.x_location_id else 'N/A'}"
            ),
        }

        po = PO.create(po_vals)

        # Add a line item for the estimated cost
        self.env['purchase.order.line'].create({
            'order_id': po.id,
            'name': (
                f"Maintenance: {self.name}"
                f"{' — ' + self.equipment_id.name if self.equipment_id else ''}"
            ),
            'product_qty': 1,
            'price_unit': self.x_estimated_cost,
            'product_id': self._get_maintenance_service_product().id,
            'product_uom_id': self.env.ref('uom.product_uom_unit').id,
        })

        self.message_post(
            body=_(
                "<b>Purchase Order Request Created</b><br/>"
                "PO: <a href='#' data-oe-model='purchase.order' "
                "data-oe-id='%(po_id)s'>%(po_name)s</a><br/>"
                "Amount: $%(amount)s",
                po_id=po.id,
                po_name=po.name,
                amount=f"{self.x_estimated_cost:,.2f}",
            ),
            subtype_xmlid='mail.mt_comment',
        )

        return {
            'type': 'ir.actions.act_window',
            'name': _("Purchase Order Request"),
            'res_model': 'purchase.order',
            'res_id': po.id,
            'view_mode': 'form',
        }

    def action_view_purchase_orders(self):
        """Smart button: show all POs linked to this maintenance request."""
        self.ensure_one()
        action = {
            'type': 'ir.actions.act_window',
            'name': _("Purchase Orders"),
            'res_model': 'purchase.order',
            'domain': [('x_maintenance_request_id', '=', self.id)],
            'view_mode': 'list,form',
        }
        if self.x_purchase_order_count == 1:
            action['res_id'] = self.x_purchase_order_ids[0].id
            action['view_mode'] = 'form'
        return action

    @api.model
    def _get_maintenance_service_product(self):
        """Get or create a generic 'Maintenance Service' product for PO lines."""
        product = self.env.ref(
            'elkspurchase.product_maintenance_service',
            raise_if_not_found=False,
        )
        if not product:
            product = self.env['product.product'].search([
                ('name', '=', 'Maintenance Service'),
                ('type', '=', 'service'),
            ], limit=1)
        if not product:
            product = self.env['product.product'].create({
                'name': 'Maintenance Service',
                'type': 'service',
                'purchase_ok': True,
                'sale_ok': False,
            })
        return product
