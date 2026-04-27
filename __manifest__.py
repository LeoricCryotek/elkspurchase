# -*- coding: utf-8 -*-
{
    "name": "Elks Lodge Purchase Requests",
    "version": "19.0.2.0",
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
    "depends": [
        "purchase",
        "elksmaintenance",
        "elksfrs",
    ],
    "data": [
        "security/elkspurchase_groups.xml",
        "security/ir.model.access.csv",
        "data/mail_template_data.xml",
        "wizard/reject_wizard_views.xml",
        "report/purchase_report_templates.xml",
        "views/purchase_order_views.xml",
        "views/maintenance_request_views.xml",
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
