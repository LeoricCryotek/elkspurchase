# -*- coding: utf-8 -*-
{
    "name": "Elks Lodge Purchase Requests",
    "version": "19.0.3.0",
    "category": "Elks Lodge/Purchase",
    "summary": "Requisition → Board → Floor → Purchase Order approval workflow",
    "description": """
Simple Elks Lodge purchase approval:

- All requests start as Requisitions
- Submit to Board → Board approves → Floor votes
- Floor approves → becomes a Purchase Order
- Requesting department & committee tracking
- GL account & budget line integration
""",
    "author": "Lewiston Elks Lodge #896",
    "license": "LGPL-3",
    # elksmaintenance is intentionally NOT a dependency.  The maintenance
    # ↔ purchase integration lives in the auto_install bridge module
    # ``elksmaintenance_purchase``, which loads only when both halves
    # are installed.  This keeps elkspurchase installable on an Odoo
    # database that does not run the lodge maintenance ticket system.
    "depends": [
        "purchase",
        "elksfrs",
    ],
    "data": [
        "security/elkspurchase_groups.xml",
        "security/ir.model.access.csv",
        "data/mail_template_data.xml",
        "wizard/reject_wizard_views.xml",
        "report/purchase_report_templates.xml",
        "views/purchase_order_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "elkspurchase/static/src/views/purchase_dashboard.xml",
            "elkspurchase/static/src/views/purchase_dashboard.js",
        ],
    },
    "installable": True,
    "application": False,
    "pre_init_hook": "_pre_init_migrate_approval_states",
}
