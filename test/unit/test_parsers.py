from pathlib import Path

import pytest
from app.ingestion.parsers import (
    ParseError,
    csv_metadata,
    csv_preview,
    inspect_file,
    read_csv,
)

DATA = Path(__file__).resolve().parents[1] / "data" / "demo"


def test_csv_quality_and_null_json():
    metadata = csv_metadata(read_csv(DATA / "sales.csv"))
    assert metadata["row_count"] == 3
    assert metadata["missing_cells"] == 1
    assert metadata["columns"][2]["missing_count"] == 1
    preview = csv_preview(DATA / "sales.csv", 2, 2)
    assert len(preview["rows"]) == 1
    assert preview["rows"][0]["revenue"] == ""


@pytest.mark.parametrize("filename", ["duplicate_columns.csv", "invalid_rows.csv", "empty.csv"])
def test_invalid_csv(filename):
    with pytest.raises(ParseError):
        read_csv(DATA / filename)


def test_csv_encoding_and_disguised_pdf(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_bytes("地区,销售额\n华东,100\n".encode("gbk"))
    with pytest.raises(ParseError, match="UTF-8"):
        read_csv(path)
    path.write_bytes((DATA / "quarterly_report.pdf").read_bytes())
    with pytest.raises(ParseError):
        read_csv(path)


def test_pdf_pages_and_unreadable_documents():
    assert inspect_file(DATA / "quarterly_report.pdf", "pdf")["page_count"] == 2
    for filename in ["damaged.pdf", "no_text.pdf"]:
        with pytest.raises(ParseError):
            inspect_file(DATA / filename, "pdf")


def test_blank_csv_data_and_non_finite_warning(tmp_path):
    path = tmp_path / "values.csv"
    path.write_text("value\n   \n", encoding="utf-8")
    with pytest.raises(ParseError, match="没有数据行"):
        read_csv(path)
    path.write_text("value\ninf\n-inf\n1\n", encoding="utf-8")
    metadata = csv_metadata(read_csv(path))
    assert "2 个无穷数值" in metadata["warnings"][1]
    assert csv_preview(path, 1, 20)["rows"][0]["value"] == "inf"


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_csv_preview_preserves_cell_text_and_record_pages(tmp_path, newline):
    path = tmp_path / "original.csv"
    content = 'id,amount,text,blank\n0001,01.20,NA,\n0002,1e3,"两行\n文字",\n'
    path.write_bytes(content.replace("\n", newline).encode("utf-8-sig"))
    assert csv_preview(path, 1, 1)["rows"] == [
        {"id": "0001", "amount": "01.20", "text": "NA", "blank": ""}
    ]
    assert csv_preview(path, 2, 1)["rows"][0]["text"] == "两行" + newline + "文字"
    assert csv_preview(path, 2, 1)["rows"][0]["amount"] == "1e3"


def test_encrypted_pdf_is_rejected(tmp_path):
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=10, height=10)
    writer.encrypt("TEST_PASSWORD")
    path = tmp_path / "encrypted.pdf"
    writer.write(path)
    with pytest.raises(ParseError, match="加密"):
        inspect_file(path, "pdf")
