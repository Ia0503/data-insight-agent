"""Independently audit raw acceptance fixtures and protect existing projects."""

import csv
import hashlib
import importlib.util
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/comprehensive"
EXPECTED = json.loads((DATA / "expected.json").read_text(encoding="utf-8"))


def load_rows(name):
    with (DATA / name).open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


@pytest.mark.parametrize("month", ["2026-06", "2026-07", "2026-08", "2026-09"])
def test_raw_orders_match_handwritten_monthly_answers(month):
    rows = load_rows("订单明细.csv")
    assert len(rows) == 2440
    assert len({row["订单编号"] for row in rows}) == 2440
    paid = [
        row
        for row in rows
        if row["订单状态"] != "cancelled"
        and datetime.fromisoformat(row["支付时间"].replace("Z", "+00:00"))
        .astimezone(ZoneInfo("Asia/Shanghai"))
        .strftime("%Y-%m")
        == month
    ]
    gross = sum((Decimal(row["实付金额"]) for row in paid), Decimal(0))
    refund = sum((Decimal(row["累计退款"]) for row in paid), Decimal(0))
    refund_count = sum(Decimal(row["累计退款"]) > 0 for row in paid)
    gold = EXPECTED["monthly"][month]
    assert len(paid) == 600
    assert gross == Decimal(gold["paid_sales"])
    assert gross - refund == Decimal(gold["net_sales"])
    assert refund_count == gold["refund_orders"]
    assert Decimal(refund_count) / len(paid) * 100 == Decimal(gold["refund_rate"])


def test_cross_month_dates_and_multiline_cells_retain_logical_records():
    rows = load_rows("订单明细.csv")
    for month, record in zip(range(6, 10), EXPECTED["boundary_records"], strict=True):
        row = rows[record - 1]
        parsed = datetime.fromisoformat(row["支付时间"].replace("Z", "+00:00"))
        assert parsed.month == month - 1
        assert (
            parsed.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat() == f"2026-{month:02}-01"
        )
        assert row["订单编号"] == f"{record:06}"
    feedback = load_rows("客户反馈.csv")
    assert len(feedback) == 120
    assert feedback[0]["客户编号"] == "NA"
    assert feedback[90]["反馈编号"] == "F0091"
    assert (
        "\n" in feedback[90]["反馈内容"]
        and ", " in feedback[90]["反馈内容"]
        and '"引号"' in feedback[90]["反馈内容"]
    )


def test_all_fixture_hashes_and_pdf_pages():
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["fictional"] is True
    assert len(manifest["assets"]) == 8
    assert (
        hashlib.sha256((DATA / "expected.json").read_bytes()).hexdigest()
        == manifest["expected_sha256"]
    )
    for name, asset in manifest["assets"].items():
        content = (DATA / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == asset["sha256"]
        assert len(content) == asset["bytes"]
        if name.endswith(".pdf"):
            reader = PdfReader(DATA / name)
            assert len(reader.pages) == 3
            assert all(page.extract_text().strip() for page in reader.pages)


def test_regeneration_keeps_imported_asset_bytes_stable(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "acceptance_generator", ROOT / "generators/generate_comprehensive.py"
    )
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    generator.generate(tmp_path)
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    for name in manifest["assets"]:
        assert (tmp_path / name).read_bytes() == (DATA / name).read_bytes()


@pytest.mark.parametrize("scenario", ["wrong_description", "duplicate_name", "missing_read_only"])
def test_import_does_not_write_to_ambiguous_or_foreign_project(tmp_path, monkeypatch, scenario):
    spec = importlib.util.spec_from_file_location("acceptance_seed", ROOT / "seed_comprehensive.py")
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    monkeypatch.setattr(seed, "STATE", tmp_path / "project.json")
    matches = (
        []
        if scenario == "missing_read_only"
        else [
            {
                "id": "existing",
                "name": EXPECTED["project_name"],
                "description": "another user project",
            }
        ]
    )
    if scenario == "duplicate_name":
        matches *= 2

    def handler(request):
        assert request.method == "GET", "Preparation must not mutate an unrelated project."
        return httpx.Response(200, json=matches)

    with (
        httpx.Client(
            base_url="http://127.0.0.1:8000/api", transport=httpx.MockTransport(handler)
        ) as client,
        pytest.raises(RuntimeError),
    ):
        seed.choose_project(client, EXPECTED, scenario == "missing_read_only")
