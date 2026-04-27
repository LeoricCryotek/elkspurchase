/** @odoo-module **/
import { PurchaseDashBoard } from "@purchase/views/purchase_dashboard";
import { patch } from "@web/core/utils/patch";

patch(PurchaseDashBoard.prototype, {});
PurchaseDashBoard.template = "elkspurchase.PurchaseDashboard";
