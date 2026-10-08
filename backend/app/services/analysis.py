import hashlib
import logging
import re
import threading
from collections import OrderedDict
from datetime import date, datetime, timedelta
from decimal import Decimal, DecimalException, InvalidOperation
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
from app.models.analysis import DatasetMapping
from app.models.workspace import DataSource
from app.schemas.analysis import MetricInput, ToolInput
from app.services.sources import source_path
from fastapi import HTTPException
from sqlalchemy import select

logger = logging.getLogger(__name__)
FIELDS = ("order_id", "paid_at", "paid_amount", "refund_amount", "region", "product", "status")
METRIC_VERSION = "orders-v1"
_cache: OrderedDict[str, pd.DataFrame] = OrderedDict()
_cache_lock = threading.Lock()
CACHE_BYTES = 32 * 1024 * 1024


def file_hash(path: Path) -> str:
    try:
        with path.open("rb") as file:
            return hashlib.file_digest(file, "sha256").hexdigest()
    except OSError as exc:
        raise HTTPException(409, "原文件不可用，请重新上传。") from exc


def dataset(source):
    if source.kind != "csv" or source.status != "ready":
        raise HTTPException(409, "分析需要已就绪的 CSV 数据源。")
    path = source_path(source)
    digest = file_hash(path)
    with _cache_lock:
        if digest in _cache:
            _cache.move_to_end(digest)
            return _cache[digest].copy(deep=True), digest
    try:
        # 业务读取保留前导零、NA 字符串和原始金额；不复用步骤一自动类型推断结果。
        frame = pd.read_csv(
            path, dtype=str, keep_default_na=False, encoding="utf-8-sig", nrows=100_001
        )
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        raise HTTPException(409, "CSV 无法读取，请检查原文件。") from exc
    if len(frame) > 100_000 or len(frame.columns) > 100 or frame.empty:
        raise HTTPException(422, "分析仅支持 1～100000 条记录、最多 100 列。")
    size = int(frame.memory_usage(deep=True).sum())
    with _cache_lock:
        if size <= CACHE_BYTES:
            while (
                _cache
                and sum(int(f.memory_usage(deep=True).sum()) for f in _cache.values()) + size
                > CACHE_BYTES
            ):
                _cache.popitem(last=False)
            _cache[digest] = frame
    return frame.copy(deep=True), digest


def money(raw: str) -> Decimal:
    if not re.fullmatch(r"\d{1,12}(?:\.\d{1,2})?", raw.strip()):
        raise ValueError("金额必须为非负十进制数，最多两位小数。")
    return Decimal(raw.strip())


def paid_date(raw: str) -> date:
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone(ZoneInfo("Asia/Shanghai"))
    except (ValueError, OverflowError) as exc:
        raise ValueError("支付日期无效或时区转换超出支持范围。") from exc
    return parsed.date()


def validate_orders(frame, fields):
    if set(fields) != set(FIELDS) or len(set(fields.values())) != len(FIELDS):
        raise HTTPException(422, "请为七个业务字段分别选择不同的 CSV 列。")
    if any(column not in frame.columns for column in fields.values()):
        raise HTTPException(422, "映射的列不存在。")
    rows, issues = [], []
    ids = set()
    for ordinal, original in enumerate(frame.to_dict("records"), 1):
        item = {key: str(original[column]).strip() for key, column in fields.items()}
        try:
            if not item["order_id"] or item["order_id"] in ids:
                raise ValueError("订单编号为空或重复。")
            ids.add(item["order_id"])
            if not item["region"] or not item["product"]:
                raise ValueError("地区和产品不能为空。")
            if item["status"] not in {"paid", "refunded", "cancelled"}:
                raise ValueError("状态仅支持 paid、refunded、cancelled。")
            paid, refund = money(item["paid_amount"]), money(item["refund_amount"])
            if refund > paid:
                raise ValueError("累计退款不能超过实付金额。")
            if item["status"] == "cancelled":
                if paid or refund:
                    raise ValueError("取消订单金额必须为零。")
                day = None
            else:
                day = paid_date(item["paid_at"])
                if item["status"] == "refunded" and not refund:
                    raise ValueError("refunded 状态需要正退款金额。")
            rows.append(
                {
                    **item,
                    "paid_amount": paid,
                    "refund_amount": refund,
                    "day": day,
                    "record": ordinal,
                }
            )
        except (ValueError, InvalidOperation) as exc:
            issues.append(
                {
                    "record": ordinal,
                    "message": str(exc)
                    if not isinstance(exc, InvalidOperation)
                    else "金额格式错误。",
                }
            )
    return rows, issues


def save_mapping(db, source, fields):
    frame, digest = dataset(source)
    _, issues = validate_orders(frame, fields)
    if issues:
        return {
            "valid": False,
            "row_count": len(frame),
            "issue_count": len(issues),
            "issues": issues[:20],
            "issues_truncated": len(issues) > 20,
        }
    # 锁定始终存在的数据源行，串行化首次创建和后续更新；映射行可能尚不存在。
    db.scalar(select(DataSource).where(DataSource.id == source.id).with_for_update())
    mapping = db.get(DatasetMapping, source.id)
    if not mapping:
        mapping = DatasetMapping(source_id=source.id)
        db.add(mapping)
    mapping.fields, mapping.content_hash, mapping.version = fields, digest, uuid4()
    db.commit()
    logger.info(
        "event=mapping_validated source_id=%s version=%s rows=%s",
        source.id,
        mapping.version,
        len(frame),
    )
    return {
        "valid": True,
        "row_count": len(frame),
        "issue_count": 0,
        "issues": [],
        "version": str(mapping.version),
        "fields": fields,
    }


def provenance(source, digest, records):
    return {
        "source_id": str(source.id),
        "filename": source.filename,
        "content_hash": digest,
        "record_count": len(records),
        "records": records[:100],
        "records_truncated": len(records) > 100,
        "locator": "CSV 数据记录序号，从 1 开始，不是物理行号。",
    }


def controlled_tool(source, data: ToolInput):
    frame, digest = dataset(source)
    frame.index = range(1, len(frame) + 1)
    if data.tool == "get_schema":
        return {
            "tool": data.tool,
            "row_count": len(frame),
            "columns": [
                {
                    "name": c,
                    "storage_type": "text",
                    "blank_count": int(frame[c].str.strip().eq("").sum()),
                }
                for c in frame.columns
            ],
            "evidence": provenance(source, digest, []),
        }
    for condition in data.filters:
        if condition.field not in frame.columns:
            raise HTTPException(422, "筛选列不存在。")
        values, target = frame[condition.field], condition.value
        if condition.operator in {"eq", "ne", "contains"}:
            mask = (
                values.eq(target)
                if condition.operator == "eq"
                else values.ne(target)
                if condition.operator == "ne"
                else values.str.contains(target, regex=False)
            )
        else:
            try:
                values = values.map(Decimal)
                target = Decimal(target)
                if not target.is_finite() or not all(v.is_finite() for v in values):
                    raise ValueError
            except (InvalidOperation, ValueError) as exc:
                raise HTTPException(422, "数值比较要求整列及筛选值均为有限数值。") from exc
            mask = {"gt": values.gt, "gte": values.ge, "lt": values.lt, "lte": values.le}[
                condition.operator
            ](target)
        frame = frame.loc[mask]
    evidence = provenance(source, digest, list(frame.index))
    if data.tool == "filter_data":
        return {
            "tool": data.tool,
            "filters": [f.model_dump() for f in data.filters],
            "total": len(frame),
            "truncated": len(frame) > data.limit,
            "rows": [
                {"record": int(i), "values": r.to_dict()}
                for i, r in frame.head(data.limit).iterrows()
            ],
            "evidence": evidence,
        }
    if data.group not in frame.columns or (
        data.aggregation != "count" and data.value not in frame.columns
    ):
        raise HTTPException(422, "分组或聚合列不存在。")
    if frame[data.group].nunique() > 1000:
        raise HTTPException(422, "分组数量超过 1000，请先筛选。")
    groups = []
    for key, part in frame.groupby(data.group, sort=True, dropna=False):
        try:
            values = list(part[data.value].map(Decimal)) if data.aggregation != "count" else []
            if any(not v.is_finite() for v in values):
                raise ValueError
            number = (
                len(part)
                if data.aggregation == "count"
                else {
                    "sum": lambda: sum(values, Decimal(0)),
                    "mean": lambda: sum(values, Decimal(0)) / len(values),
                    "min": lambda: min(values),
                    "max": lambda: max(values),
                }[data.aggregation]()
            )
        except (DecimalException, ValueError, OverflowError) as exc:
            raise HTTPException(
                422, "聚合要求有限数值且结果不超出数值范围，不会忽略无效数据。"
            ) from exc
        groups.append(
            {
                "group": str(key),
                "value": str(number),
                "records": list(part.index[:100]),
                "record_count": len(part),
            }
        )
    return {
        "tool": data.tool,
        "aggregation": data.aggregation,
        "groups": groups[: data.limit],
        "truncated": len(groups) > data.limit,
        "evidence": evidence,
    }


def metric_value(rows, metric):
    if metric == "paid_orders":
        return Decimal(len(rows)), "单"
    if metric == "refund_rate":
        return (
            Decimal(sum(r["refund_amount"] > 0 for r in rows)) / len(rows) * 100 if rows else None
        ), "%"
    value = sum(
        (r["paid_amount"] - (r["refund_amount"] if metric == "net_sales" else 0) for r in rows),
        Decimal(0),
    )
    return value, "元"


def display(value):
    return str(value.quantize(Decimal("0.01"))) if value is not None else None


def order_statistics(rows):
    # 与退款比例共用已验证、按业务日期筛选的完整记录，不能从展示样本推断有无退款。
    return {
        "timezone": "Asia/Shanghai",
        "paid_order_count": len(rows),
        "refund_order_count": sum(row["refund_amount"] > 0 for row in rows),
        "refund_basis": "累计退款金额大于零的订单，按支付业务日期归属；不是实际退款发生日期。",
    }


def previous_period(start, end):
    # 完整自然月比较上一个自然月；其他范围比较紧邻的等天数区间。
    next_day = end + timedelta(days=1)
    if (
        start.day == 1
        and next_day.day == 1
        and (next_day.year * 12 + next_day.month) - (start.year * 12 + start.month) == 1
    ):
        previous_end = start - timedelta(days=1)
        return previous_end.replace(day=1), previous_end
    length = (end - start).days + 1
    return start - timedelta(days=length), start - timedelta(days=1)


def calculate_metric(db, source, data: MetricInput):
    frame, digest = dataset(source)
    mapping = db.get(DatasetMapping, source.id)
    if not mapping or mapping.content_hash != digest:
        raise HTTPException(409, "请先完成并保存有效字段映射；文件变更后需要重新校验。")
    rows, issues = validate_orders(frame, mapping.fields)
    if issues:
        raise HTTPException(409, "业务数据校验失败，请重新配置字段映射。")
    eligible = [
        r
        for r in rows
        if r["status"] != "cancelled"
        and (data.region is None or r["region"] == data.region)
        and (data.product is None or r["product"] == data.product)
    ]
    selected = [r for r in eligible if data.start <= r["day"] <= data.end]
    value, unit = metric_value(selected, data.metric)
    warnings = [
        "退款按文件快照中的累计退款归属到支付日期，不代表本期实际退款现金流。",
        "指标和检索证据用于描述事实，不能直接证明因果关系。",
    ]
    if not selected:
        warnings.append("所选范围没有已支付订单；计数和金额为零，退款比例无定义。")
    comparison = None
    if data.compare_previous:
        start, end = previous_period(data.start, data.end)
        previous = [r for r in eligible if start <= r["day"] <= end]
        before, _ = metric_value(previous, data.metric)
        change = (value - before) / before * 100 if before and value is not None else None
        comparison = {
            "start": str(start),
            "end": str(end),
            "value": display(before),
            "change_percent": display(change),
            "reason": "基期为零或比例无定义时，不计算增长率。" if change is None else None,
            "statistics": order_statistics(previous),
            "evidence": provenance(source, digest, [row["record"] for row in previous]),
        }
    groups = []
    if data.group:
        buckets = {}
        for row in selected:
            key = row["day"].strftime("%Y-%m") if data.group == "month" else row[data.group]
            buckets.setdefault(key, []).append(row)
        if len(buckets) > 1000:
            raise HTTPException(422, "指标分组超过 1000，请缩小范围。")
        for key, bucket in sorted(buckets.items()):
            number, _ = metric_value(bucket, data.metric)
            contribution = (
                number / value * 100
                if value and number is not None and data.metric != "refund_rate"
                else None
            )
            groups.append(
                {
                    "group": key,
                    "value": display(number),
                    "contribution_percent": display(contribution),
                    "records": [r["record"] for r in bucket[:100]],
                    "record_count": len(bucket),
                }
            )
    logger.info(
        "event=metric_calculated project_id=%s source_id=%s metric=%s rows=%s",
        source.project_id,
        source.id,
        data.metric,
        len(selected),
    )
    return {
        "metric": data.metric,
        "metric_version": METRIC_VERSION,
        "mapping_version": str(mapping.version),
        "value": display(value),
        "unit": unit,
        "filters": data.model_dump(mode="json"),
        "comparison": comparison,
        "statistics": order_statistics(selected),
        "groups": groups,
        "warnings": warnings,
        "evidence": provenance(source, digest, [r["record"] for r in selected]),
    }
