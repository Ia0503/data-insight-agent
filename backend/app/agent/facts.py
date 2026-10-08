"""只从服务端工具结果构造事实与引用清单；模型只能选择事实 ID。"""

from decimal import Decimal

LABELS = {
    "net_sales": "净销售额",
    "paid_sales": "实付金额",
    "paid_orders": "已支付订单数",
    "refund_rate": "累计退款订单比例",
}


def scope_text(data, period):
    filters = data["filters"]
    parts = [
        f"数据源“{data['evidence']['filename']}”：{period['start']}至{period['end']}（中国标准时间）"
    ]
    for field, label in (("region", "地区"), ("product", "产品")):
        if filters.get(field) is not None:
            parts.append(f"{label}={filters[field]}")
    return "，".join(parts)


def build_catalog(results):
    facts, evidence = {}, {}

    def add(identifier, text, evidence_ids, category):
        facts[identifier] = {
            "id": identifier,
            "text": text,
            "evidence_ids": evidence_ids,
            "category": category,
        }

    for result in results:
        identifier, data = result["id"], result["data"]
        if result["tool"] == "search_evidence":
            for hit in data["results"]:
                reference = f"{identifier}:{hit['chunk_id']}"
                evidence[reference] = {"id": reference, "kind": "document"}
                # 摘录仅证明资料写了什么，不能把资料声明当作已核实的业务事实。
                add(
                    f"{reference}:quote",
                    f"资料原文摘录（内容未经事实核实）：“{hit['text']}”",
                    [reference],
                    "document_quote",
                )
        elif "evidence" in data:
            evidence[identifier] = {"id": identifier, "kind": "csv"}
        if result["tool"] != "calculate_metric" or data.get("metric") not in LABELS:
            continue
        label = LABELS[data["metric"]]
        scope = scope_text(data, data["filters"])
        value = data["value"]
        add(
            f"{identifier}:metric",
            f"{scope}，{label}为{value}{data['unit']}。"
            if value is not None
            else f"{scope}，{label}无定义，不能给出数值。",
            [identifier],
            "calculated_metric",
        )
        periods = [("orders", data["filters"], data.get("statistics"), [identifier])]
        before = data.get("comparison")
        if before and before.get("evidence"):
            previous_id = f"{identifier}:previous"
            evidence[previous_id] = {"id": previous_id, "kind": "csv"}
            periods.append(("previous_orders", before, before.get("statistics"), [previous_id]))
            if value is not None and before["value"] is not None:
                difference = Decimal(value) - Decimal(before["value"])
                direction = "高于" if difference > 0 else "低于" if difference < 0 else "等于"
                add(
                    f"{identifier}:trend",
                    f"{scope}，{label}{direction}基期（{before['start']}至{before['end']}）；"
                    f"基期值为{before['value']}{data['unit']}。",
                    [identifier, previous_id],
                    "calculated_comparison",
                )
        for suffix, period, statistics, evidence_ids in periods:
            if statistics is None:
                continue  # 旧工具结果缺少统计时不推断；历史报告本身不重新生成或验证。
            add(
                f"{identifier}:{suffix}",
                f"{scope_text(data, period)}，已支付订单{statistics['paid_order_count']}单，"
                f"累计退款金额大于零的订单{statistics['refund_order_count']}单。"
                "退款按支付业务日期归属，不代表本期实际发生的退款现金流。",
                evidence_ids,
                "calculated_orders",
            )
    return facts, evidence
