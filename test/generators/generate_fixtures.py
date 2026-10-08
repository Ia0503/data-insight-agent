"""Small deterministic files for ingestion checks; not a business evaluation dataset."""

from pathlib import Path

from pypdf import PdfWriter
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1] / "data" / "demo"
ROOT.mkdir(parents=True, exist_ok=True)
(ROOT / "sales.csv").write_text(
    "date,region,revenue,orders\n2026-08-01,East,1000,10\n2026-09-01,East,800,8\n2026-09-02,West,,5\n",
    encoding="utf-8-sig",
)
(ROOT / "duplicate_columns.csv").write_text("amount,amount\n1,2\n", encoding="utf-8")
(ROOT / "invalid_rows.csv").write_text("amount,orders\n1,2,3\n", encoding="utf-8")
(ROOT / "empty.csv").write_bytes(b"")
(ROOT / "damaged.pdf").write_bytes(b"%PDF-1.4\nnot a valid document")
writer = PdfWriter()
writer.add_blank_page(width=595, height=842)
with (ROOT / "no_text.pdf").open("wb") as file:
    writer.write(file)
pdf = canvas.Canvas(str(ROOT / "quarterly_report.pdf"), pageCompression=0, invariant=1)
pdf.setTitle("Fictional Q3 Report - Test Fixture")
pdf.drawString(72, 760, "Fictional SaaS Q3 report - ingestion test")
pdf.drawString(72, 730, "September East-region revenue: 800; August: 1000.")
pdf.showPage()
pdf.drawString(72, 760, "Version 3.2 was released in September 2026.")
pdf.save()
print("Generated ingestion fixtures in test/data/demo.")
