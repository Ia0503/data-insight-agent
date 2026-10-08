import logging
import re

from pydantic import ValidationError

from app.agent.facts import LABELS, build_catalog, scope_text
from app.schemas.agent import FactReference, ReportDraft

logger = logging.getLogger(__name__)


class ReportError(Exception):
    pass


def schema_diagnostics(exc: ValidationError, schema):
    """保留已知字段位置和错误代码，不输出未知键名、原文或无效输入值。"""
    fields = set(schema.model_fields) | {
        "kind",
        "text",
        "evidence_ids",
        "result_id",
        "fact_id",
        "FactReference",
        "Finding",
        "ChartReference",
    }
    for definition in schema.model_json_schema().get("$defs", {}).values():
        fields.update(definition.get("properties", {}))
    return [
        {
            "type": item["type"],
            "path": [
                part if isinstance(part, int) or part in fields else "unknown_field"
                for part in item["loc"]
            ],
        }
        for item in exc.errors(include_input=False, include_context=False, include_url=False)[:4]
    ]


def reference_diagnostics(value, results: list[dict]):
    """结构尚未通过时也检查已知引用字段，只返回受控位置，不回显无效编号。"""
    if not isinstance(value, dict):
        return []
    facts, evidence = build_catalog(results)
    metrics = {item["id"] for item in results if item["tool"] == "calculate_metric"}
    charts = {
        item["id"]
        for item in results
        if item["tool"] in ("calculate_metric", "group_by") and item["data"].get("groups")
    }
    issues = []

    def check(identifier, choices, path):
        if isinstance(identifier, str) and identifier not in choices:
            issues.append({"type": "unavailable_reference", "path": path})

    findings = value.get("findings")
    if isinstance(findings, list):
        for index, finding in enumerate(findings[:12]):
            if not isinstance(finding, dict):
                continue
            check(finding.get("fact_id"), facts, ["findings", index, "fact_id"])
            references = finding.get("evidence_ids")
            if isinstance(references, list):
                for position, identifier in enumerate(references[:12]):
                    check(identifier, evidence, ["findings", index, "evidence_ids", position])
    identifiers = value.get("metric_ids")
    if isinstance(identifiers, list):
        for index, identifier in enumerate(identifiers[:12]):
            check(identifier, metrics, ["metric_ids", index])
    specifications = value.get("chart_specs")
    if isinstance(specifications, list):
        for index, chart in enumerate(specifications[:4]):
            if isinstance(chart, dict):
                check(chart.get("result_id"), charts, ["chart_specs", index, "result_id"])
    return issues[:12]


def validate_report(draft: ReportDraft, results: list[dict], *, log_validation=True):
    facts, _ = build_catalog(results)
    evidence = {}
    metrics = {}
    charts = {}
    for item in results:
        identifier, data = item["id"], item["data"]
        if item["tool"] == "search_evidence":
            for hit in data["results"]:
                evidence[f"{identifier}:{hit['chunk_id']}"] = {**hit, "kind": "document"}
        elif "evidence" in data:
            evidence[identifier] = {"kind": "csv", **data["evidence"]}
        if item["tool"] == "calculate_metric":
            metrics[identifier] = {"id": identifier, **data}
            if data.get("comparison") and data["comparison"].get("evidence"):
                evidence[f"{identifier}:previous"] = {
                    "kind": "csv",
                    **data["comparison"]["evidence"],
                }
        if data.get("groups") and item["tool"] in ("calculate_metric", "group_by"):
            if item["tool"] == "calculate_metric":
                group = {"region": "地区", "product": "产品", "month": "月份"}.get(
                    data["filters"].get("group"), "分组"
                )
                title = (
                    f"{scope_text(data, data['filters'])}，{LABELS[data['metric']]}按{group}分组"
                )
            else:
                aggregation = {
                    "count": "计数",
                    "sum": "合计",
                    "mean": "平均",
                    "min": "最小值",
                    "max": "最大值",
                }[data["aggregation"]]
                title = f"数据源“{data['evidence']['filename']}”的受控分组{aggregation}（按原始列值筛选）"
            charts[identifier] = {
                "title": title,
                "labels": [value["group"] for value in data["groups"]],
                "values": [value["value"] for value in data["groups"]],
                "unit": data.get("unit", "条" if data.get("aggregation") == "count" else ""),
                "time_axis": item["tool"] == "calculate_metric"
                and data["filters"].get("group") == "month",
                "evidence_ids": [identifier],
            }
    text = {
        "title": draft.title,
        **{f"recommendations[{i}]": value for i, value in enumerate(draft.recommendations)},
        **{f"limitations[{i}]": value for i, value in enumerate(draft.limitations)},
        **{
            f"findings[{i}].text": finding.text
            for i, finding in enumerate(draft.findings)
            if not isinstance(finding, FactReference)
        },
        **{f"chart_specs[{i}].title": chart.title for i, chart in enumerate(draft.chart_specs)},
    }
    # 自由叙述不接收数字；事实、摘要、指标和图表都从真实结果生成。
    # 推断、建议及资料原文的真伪仍不能由结构/引用校验自动证明。
    for path, value in text.items():
        if re.search(r"\d", value):
            raise ReportError(f"{path} 含数字，请将数值放在服务端事实、指标卡片或图表中。")
    findings, chosen = [], set()
    for index, finding in enumerate(draft.findings):
        if isinstance(finding, FactReference):
            if finding.fact_id not in facts or finding.fact_id in chosen:
                raise ReportError(
                    f"findings[{index}].fact_id 未匹配可选事实或重复，请从 fact_catalog 选择。"
                )
            chosen.add(finding.fact_id)
            fact = facts[finding.fact_id]
            findings.append(
                {
                    "kind": "fact",
                    "text": fact["text"],
                    "evidence_ids": fact["evidence_ids"],
                    "fact_id": fact["id"],
                    "category": fact["category"],
                }
            )
            continue
        if set(finding.evidence_ids) - evidence.keys():
            raise ReportError(
                f"findings[{index}].evidence_ids 引用了不存在的证据，请从 evidence_catalog 选择。"
            )
        findings.append(finding.model_dump())
    if set(draft.metric_ids) - metrics.keys() or len(set(draft.metric_ids)) != len(
        draft.metric_ids
    ):
        raise ReportError("报告引用了不存在或重复的指标。")
    generated_charts = []
    for chart in draft.chart_specs:
        if chart.result_id not in charts:
            raise ReportError("图表必须引用已计算的真实分组结果。")
        generated_charts.append({**chart.model_dump(), **charts[chart.result_id]})
    used = {identifier for finding in findings for identifier in finding["evidence_ids"]} | set(
        draft.metric_ids
    )
    used.update(chart.result_id for chart in draft.chart_specs)
    limitations = list(
        dict.fromkeys(
            [
                *draft.limitations,
                "检索和统计只提供描述性证据，不能直接证明因果关系。",
                "计算事实由服务端生成；资料摘录仅证明原文内容。推断、建议及资料真伪仍需人工核对，不能自动证明因果。",
                *(warning for metric in metrics.values() for warning in metric.get("warnings", [])),
            ]
        )
    )
    if len(results) >= 12:
        limitations.append("任务达到工具调用预算，仅基于已取得的结果生成报告。")
    # 摘要仅复用计算事实，防止自由摘要重新引入未经校验的数量/月份判断。
    summary = "\n".join(
        item["text"] for item in findings if item.get("category", "").startswith("calculated_")
    )
    if not summary:
        summary = "现有结果未提供已选择的计算事实；请查看资料摘录、待验证推断与未知事项。"
    if log_validation:
        logger.info(
            "event=report_validated version=agent-report-v2 facts=%s findings=%s metrics=%s",
            len(chosen),
            len(findings),
            len(draft.metric_ids),
        )
    return {
        **draft.model_dump(
            exclude={"metric_ids", "chart_specs", "limitations", "findings", "summary"}
        ),
        "version": "agent-report-v2",
        "summary": summary,
        "findings": findings,
        "metrics": [metrics[identifier] for identifier in draft.metric_ids],
        "sources": [
            {"id": identifier, **evidence[identifier]}
            for identifier in sorted(used)
            if identifier in evidence
        ],
        "limitations": limitations,
        "chart_specs": generated_charts,
    }
