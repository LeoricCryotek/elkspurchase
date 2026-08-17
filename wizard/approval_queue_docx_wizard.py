# -*- coding: utf-8 -*-
"""Generate a Word (.docx) report of the Board or Floor approval queue,
for the Secretary to paste directly into meeting minutes.

Layout kept minimal and copy-paste-friendly:
  - Title line
  - One-line date + counts
  - Motion header
  - Numbered list of requisitions (PO # · Vendor · Purpose · $Amount)
  - Grand total
  - Vote-recording line

Each queue produces a distinct file named e.g.
"Board Approval Queue - 2026-08-16.docx".
"""
import base64
import io
from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import UserError

try:
    from docx import Document
    from docx.shared import Pt, Inches, RGBColor
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
except ImportError:
    Document = None  # module install guard; raise at runtime


QUEUE_TYPES = [
    ('board', 'Board Approval Queue'),
    ('floor', 'Floor Approval Queue'),
]


class ApprovalQueueDocxWizard(models.TransientModel):
    _name = "elks.approval.queue.docx.wizard"
    _description = "Approval Queue — Word Document Export"

    queue_type = fields.Selection(
        QUEUE_TYPES, string="Queue", required=True, default='board',
    )

    def action_generate_docx(self):
        """Build the .docx, save as ir.attachment, return download URL."""
        self.ensure_one()
        if Document is None:
            raise UserError(_(
                "python-docx is not installed on the server. "
                "Ask your admin to run `pip install python-docx`."
            ))

        pos = self.env['purchase.order'].search(
            [('x_approval_state', '=', self.queue_type)],
            order='x_requesting_department_id, name',
        )
        if not pos:
            raise UserError(_(
                "No requisitions are currently in the %s queue.",
                dict(QUEUE_TYPES)[self.queue_type],
            ))

        doc = self._build_docx(pos)

        # Serialize to bytes
        buf = io.BytesIO()
        doc.save(buf)
        data = buf.getvalue()

        # Save as attachment for download
        today_str = date.today().strftime("%Y-%m-%d")
        queue_label = dict(QUEUE_TYPES)[self.queue_type]
        fname = f"{queue_label} - {today_str}.docx"
        att = self.env['ir.attachment'].create({
            'name': fname,
            'datas': base64.b64encode(data),
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': (
                'application/vnd.openxmlformats-officedocument.'
                'wordprocessingml.document'
            ),
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{att.id}?download=true',
            'target': 'self',
        }

    # ------------------------------------------------------------------
    # DOCX layout
    # ------------------------------------------------------------------
    def _build_docx(self, pos):
        queue_label = dict(QUEUE_TYPES)[self.queue_type]
        stage = "Board" if self.queue_type == 'board' else "Floor"

        doc = Document()

        # Global font
        style = doc.styles['Normal']
        style.font.name = "Calibri"
        style.font.size = Pt(11)

        # ── Title ────────────────────────────────────────────────
        title = doc.add_paragraph()
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = title.add_run(queue_label)
        run.bold = True
        run.font.size = Pt(16)

        # ── Date + summary line ─────────────────────────────────
        subtotal = sum(p.amount_total for p in pos)
        over_budget = len(pos.filtered('x_has_over_budget_lines'))
        sub = doc.add_paragraph()
        sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
        sub_run = sub.add_run(
            f"Prepared {date.today().strftime('%B %d, %Y')}  "
            f"·  {len(pos)} requisition(s)  ·  Total: ${subtotal:,.2f}"
        )
        sub_run.italic = True
        sub_run.font.size = Pt(10)
        if over_budget:
            warn = doc.add_paragraph()
            warn.alignment = WD_ALIGN_PARAGRAPH.CENTER
            wr = warn.add_run(f"⚠ {over_budget} requisition(s) over budget")
            wr.italic = True
            wr.font.size = Pt(10)
            wr.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)

        doc.add_paragraph()  # spacer

        # ── Motion header ───────────────────────────────────────
        motion = doc.add_paragraph()
        m_run = motion.add_run(
            f"MOTION: To approve the following requisitions "
            f"as presented to the {stage}:"
        )
        m_run.bold = True

        # ── Requisition list ────────────────────────────────────
        for idx, po in enumerate(pos, start=1):
            p = doc.add_paragraph(style='List Number')
            # Line 1: PO # + title + vendor + amount (single paragraph)
            title_part = po.x_requisition_title or "Untitled"
            po_run = p.add_run(f"{po.name} — ")
            po_run.bold = True
            p.add_run(f"{title_part}  ·  ")
            vendor_run = p.add_run(f"{po.partner_id.display_name or '—'}")
            vendor_run.italic = True
            p.add_run("  ·  ")
            amt_run = p.add_run(f"${po.amount_total:,.2f}")
            amt_run.bold = True

            # Line 2: department / committee (indented small text)
            meta_bits = []
            if po.x_requesting_department_id:
                meta_bits.append(f"Dept: {po.x_requesting_department_id.display_name}")
            if po.x_requesting_committee_id:
                meta_bits.append(f"Committee: {po.x_requesting_committee_id.display_name}")
            if po.x_has_over_budget_lines:
                meta_bits.append("⚠ OVER BUDGET")
            if meta_bits:
                meta = doc.add_paragraph()
                meta.paragraph_format.left_indent = Inches(0.5)
                meta_run = meta.add_run("    " + " · ".join(meta_bits))
                meta_run.italic = True
                meta_run.font.size = Pt(9)
                if po.x_has_over_budget_lines:
                    meta_run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)

        # ── Grand total ─────────────────────────────────────────
        doc.add_paragraph()
        gt = doc.add_paragraph()
        gt.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        gt_run = gt.add_run(f"GRAND TOTAL: ${subtotal:,.2f}")
        gt_run.bold = True
        gt_run.font.size = Pt(12)

        # ── Vote-recording block ────────────────────────────────
        doc.add_paragraph()
        vote_intro = doc.add_paragraph()
        vi_run = vote_intro.add_run("Motion by: ______________________   Second by: ______________________")
        vi_run.font.size = Pt(11)

        vote_row = doc.add_paragraph()
        vr_run = vote_row.add_run(
            "Vote:    YES ______    NO ______    ABSTAIN ______    "
            "Motion:  ☐ Carried    ☐ Failed"
        )
        vr_run.font.size = Pt(11)

        doc.add_paragraph()  # spacer

        # ── Signature line ─────────────────────────────────────
        sig = doc.add_paragraph()
        sig_run = sig.add_run(
            f"{stage} Chair Signature: _________________________________   "
            f"Date: __________"
        )
        sig_run.font.size = Pt(10)

        return doc
