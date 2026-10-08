"""共同答案判据；不把格式校验、关键词或服务端限制当作语义正确率。"""

import csv
import io
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field


class StrictAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Filters(StrictAnswer):
    start: date
    end: date
    region: str | None = None
    product: str | None = None
    group: Literal["month", "region", "product"] | None = None


class Comparison(StrictAnswer):
    value: str | None
    change_percent: str | None


class Group(StrictAnswer):
    group: str
    value: str | None


class Metric(StrictAnswer):
    filename: str
    metric: Literal["paid_sales", "net_sales", "paid_orders", "refund_rate"]
    filters: Filters
    value: str | None
    records: list[int] = Field(max_length=100)
    groups: list[Group] = Field(default_factory=list, max_length=100)
    comparison: Comparison | None = None


class Evidence(StrictAnswer):
    filename: str
    page: int | None = None
    record: int | None = None
    quote: str = Field(min_length=1, max_length=1500)


class Finding(StrictAnswer):
    kind: Literal["fact", "inference", "unknown"]
    text: str = Field(min_length=1, max_length=1200)


class BaselineAnswer(StrictAnswer):
    title: str = Field(min_length=1, max_length=120)
    metrics: list[Metric] = Field(max_length=12)
    evidence: list[Evidence] = Field(max_length=20)
    findings: list[Finding] = Field(min_length=1, max_length=12)
    limitations: list[str] = Field(max_length=8)


def number_equal(actual, expected):
    if actual is None or expected is None:
        return actual is expected
    try:
        a, b = Decimal(str(actual)), Decimal(str(expected))
        return a.is_finite() and b.is_finite() and a == b
    except (ValueError, ArithmeticError):
        return False


def location(item):
    return item.get("filename"), item.get("page"), item.get("record")


def metric_matches(item, expected):
    fields = item.get("filters", {})
    return (
        item.get("metric") == expected["metric"]
        and item.get("filename") == expected["filename"]
        and all(
            fields.get(key) == expected.get(key) for key in ("start", "end", "region", "product")
        )
        and ("groups" not in expected or fields.get("group") == expected.get("group"))
    )


def metric_correct(item, expected):
    checks = {
        "value": number_equal(item.get("value"), expected["value"]),
        "records": sorted(item.get("records", [])) == sorted(expected["records"]),
    }
    if "groups" in expected:
        groups = item.get("groups", [])
        checks["groups"] = (
            len(groups) == len(expected["groups"])
            and len({g["group"] for g in groups}) == len(groups)
            and all(
                g["group"] in expected["groups"]
                and number_equal(g["value"], expected["groups"][g["group"]])
                for g in groups
            )
        )
    if "previous_value" in expected:
        comparison = item.get("comparison") or {}
        checks["comparison"] = "value" in comparison and number_equal(
            comparison["value"], expected["previous_value"]
        )
        checks["change"] = "change_percent" in comparison and number_equal(
            comparison["change_percent"], expected["change_percent"]
        )
    return checks


def amount(rows, metric):
    if metric == "paid_orders":
        value = Decimal(len(rows))
    elif metric == "refund_rate":
        value = (
            Decimal(sum(Decimal(row["refund_amount"]) > 0 for row in rows)) / len(rows) * 100
            if rows
            else None
        )
    else:
        value = sum(
            (
                Decimal(row["paid_amount"])
                - (Decimal(row["refund_amount"]) if metric == "net_sales" else 0)
                for row in rows
            ),
            Decimal(0),
        )
    return str(value.quantize(Decimal("0.01"))) if value is not None else None


def oracle(item, file):
    """额外历史指标独立按原始CSV复算，避免误拒正确上下文；目标答案仍用冻结手算表。"""
    fields = item["filters"]
    start, end = date.fromisoformat(fields["start"]), date.fromisoformat(fields["end"])
    if not date(1900, 1, 1) <= start <= end <= date(2100, 12, 31):
        raise ValueError("invalid_period")
    rows = []
    with file.open(encoding="utf-8-sig", newline="") as handle:
        for record, row in enumerate(csv.DictReader(handle), 1):
            if row["status"] == "cancelled":
                continue
            instant = datetime.fromisoformat(row["paid_at"].replace("Z", "+00:00"))
            day = (
                instant.astimezone(ZoneInfo("Asia/Shanghai")).date()
                if instant.tzinfo
                else instant.date()
            )
            if (fields.get("region") is None or row["region"] == fields["region"]) and (
                fields.get("product") is None or row["product"] == fields["product"]
            ):
                rows.append({**row, "day": day, "record": record})
    selected = [row for row in rows if start <= row["day"] <= end]
    expected = {
        "value": amount(selected, item["metric"]),
        "records": [row["record"] for row in selected],
    }
    if group := fields.get("group"):
        buckets = {}
        for row in selected:
            key = row["day"].strftime("%Y-%m") if group == "month" else row[group]
            buckets.setdefault(key, []).append(row)
        expected["groups"] = {
            key: amount(bucket, item["metric"]) for key, bucket in buckets.items()
        }
    if item.get("comparison"):
        previous_end = start - timedelta(days=1)
        next_day = end + timedelta(days=1)
        previous_start = (
            previous_end.replace(day=1)
            if start.day == 1
            and next_day.day == 1
            and (next_day.year * 12 + next_day.month) - (start.year * 12 + start.month) == 1
            else start - timedelta(days=(end - start).days + 1)
        )
        previous = [row for row in rows if previous_start <= row["day"] <= previous_end]
        before, current = amount(previous, item["metric"]), expected["value"]
        expected["previous_value"] = before
        expected["change_percent"] = (
            str(
                ((Decimal(current) - Decimal(before)) / Decimal(before) * 100).quantize(
                    Decimal("0.01")
                )
            )
            if before is not None and Decimal(before) and current is not None
            else None
        )
    return expected


def original_text(file: Path, *, page=None, record=None):
    if file.suffix == ".pdf" and type(page) is int and record is None and page > 0:
        from app.ingestion.parsers import extract_pdf_text, read_pdf

        return extract_pdf_text(read_pdf(file).pages[page - 1])
    if file.suffix == ".csv" and type(record) is int and page is None and record > 0:
        with file.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))[record - 1]["text"]
    raise ValueError("invalid_locator")


def citation_valid(item, selected_files):
    file = selected_files.get(item.get("filename"))
    if not file:
        return False
    try:
        if "quote" in item and file.suffix == ".csv" and item.get("page") is None:
            record, quote = item.get("record"), item["quote"]
            if (
                type(record) is not int
                or record < 1
                or not isinstance(quote, str)
                or not quote.strip()
            ):
                return False
            # 基线读到的是CSV原文，可引用完整记录；系统检索偏移仍针对text字段。
            # reader.line_num跟踪物理行，记录号不能因单元格换行而错位。
            content = file.read_text("utf-8-sig")
            lines, reader = content.splitlines(keepends=True), csv.reader(io.StringIO(content))
            header = next(reader)
            start = reader.line_num
            for ordinal, cells in enumerate(reader, 1):
                if ordinal == record:
                    raw = "".join(lines[start : reader.line_num]).rstrip("\r\n")
                    return (
                        quote in raw or any(quote in cell for cell in cells)
                        if len(cells) == len(header)
                        else False
                    )
                start = reader.line_num
            return False
        original = original_text(file, page=item.get("page"), record=item.get("record"))
        quote = item.get("quote") or item.get("text")
        if "start" in item and "end" in item:
            return (
                type(item["start"]) is int
                and type(item["end"]) is int
                and 0 <= item["start"] < item["end"] <= len(original)
                and original[item["start"] : item["end"]] == quote
            )
        return isinstance(quote, str) and bool(quote.strip()) and quote in original
    except (ValueError, KeyError, IndexError, OSError, StopIteration, csv.Error):
        return False


def agent_projection(report):
    metrics = [
        {**m, "filename": m["evidence"]["filename"], "records": m["evidence"]["records"]}
        for m in report["metrics"]
    ]
    return {
        "title": report["title"],
        "metrics": metrics,
        "evidence": [s for s in report["sources"] if s["kind"] == "document"],
        "findings": report["findings"],
        "limitations": report["limitations"],
    }


def score_answer(case, answer, selected_files):
    checks = []
    for expected in case["expected_metrics"]:
        candidates = [m for m in answer["metrics"] if metric_matches(m, expected)]
        checks.append(
            {
                "metric": expected["metric"],
                "present": bool(candidates),
                "correct": bool(candidates)
                and all(all(metric_correct(m, expected).values()) for m in candidates),
            }
        )
    numeric_valid = True
    for metric in answer["metrics"]:
        file = selected_files.get(metric.get("filename"))
        try:
            numeric_valid = (
                numeric_valid
                and file is not None
                and all(metric_correct(metric, oracle(metric, file)).values())
            )
        except (ValueError, KeyError, ArithmeticError, OSError):
            numeric_valid = False
    evidence = answer["evidence"]
    valid = [citation_valid(item, selected_files) for item in evidence]
    covered = not case["targets"] or any(
        location(item) in {location(target) for target in case["targets"]} and ok
        for item, ok in zip(evidence, valid, strict=True)
    )
    automatic = all(c["correct"] for c in checks) and numeric_valid and all(valid) and covered
    return {
        "automatic_passed": automatic,
        "metric_checks": checks,
        "all_reported_metrics_valid": numeric_valid,
        "valid_citations": sum(valid),
        "citation_count": len(evidence),
        "required_evidence_covered": covered,
        "manual_status": "pending",
        "manual_checks": case["manual_checks"],
    }


def score_retrieval(case, hits, selected_files):
    targets = {location(target) for target in case["targets"]}
    ranks = [rank for rank, hit in enumerate(hits, 1) if location(hit) in targets]
    valid = [citation_valid(hit, selected_files) for hit in hits]
    rank = min(ranks) if ranks else None
    return {
        "rank": rank,
        "hit": bool(rank) if targets else not hits,
        "negative": not targets,
        "citation_count": len(hits),
        "valid_citations": sum(valid),
        "scope_valid": all(hit.get("filename") in selected_files for hit in hits),
        "passed": (bool(rank) if targets else not hits)
        and all(valid)
        and all(hit.get("filename") in selected_files for hit in hits),
    }


def summarize(rows):
    """按全部用例计完成率；未执行/失败不能被筛出分母，人工待审也不能写为通过。"""
    result = {
        "total": len(rows),
        "completed": sum(row["status"] == "completed" for row in rows),
        "automatic_passed": sum(
            row.get("checks", {}).get(
                "automatic_passed", row.get("checks", {}).get("passed", False)
            )
            for row in rows
        ),
        "statuses": {},
    }
    for row in rows:
        result["statuses"][row["status"]] = result["statuses"].get(row["status"], 0) + 1
    positives = [row for row in rows if "targets" in row and row["targets"]]
    if positives:
        result.update(
            positive_questions=len(positives),
            hit_at_5=sum(bool(row.get("checks", {}).get("rank")) for row in positives)
            / len(positives),
            top1=sum(row.get("checks", {}).get("rank") == 1 for row in positives) / len(positives),
            mrr_at_5=sum(
                1 / row["checks"]["rank"] if row.get("checks", {}).get("rank") else 0
                for row in positives
            )
            / len(positives),
        )
    return result
