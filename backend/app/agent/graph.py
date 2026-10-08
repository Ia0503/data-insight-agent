import asyncio
import json
from time import monotonic
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from pydantic import ValidationError

from app.agent.facts import build_catalog
from app.agent.model import ModelError
from app.agent.prompts import (
    PLAN_RULES,
    REASON_RULES,
    REPORT_RULES,
    SYSTEM,
    json_content,
    repair_guidance,
    report_example,
    result_view,
    source_context,
    task_state,
)
from app.agent.report import ReportError, reference_diagnostics, schema_diagnostics, validate_report
from app.agent.tools import definitions
from app.schemas.agent import Plan, ReportDraft, TaskRequirement


class State(TypedDict):
    messages: list[dict]
    calls: list[dict]
    results: list[dict]
    plan: list[str]
    draft: dict
    report: dict


class AgentWorkflow:
    def __init__(self, run, model, executor, emit, *, deadline=None):
        self.run = run
        self.model = model
        self.executor = executor
        self.emit = emit
        self.deadline = deadline
        self.repairs = 0
        self.model_calls = 0
        self.usage = {}
        self.usage_calls = 0
        self.usage_complete_calls = 0
        self.requirements = []
        self.sources = []
        builder = StateGraph(State)
        for name in ("check", "plan", "reason", "tools", "report", "validate"):
            builder.add_node(name, getattr(self, name))
        builder.add_edge(START, "check")
        builder.add_edge("check", "plan")
        builder.add_edge("plan", "reason")
        builder.add_conditional_edges(
            "reason", lambda state: "tools" if state["calls"] else "report"
        )
        builder.add_edge("tools", "reason")
        builder.add_edge("report", "validate")
        builder.add_edge("validate", END)
        self.graph = builder.compile()

    async def ask(self, messages, tools=None):
        if self.deadline is not None and monotonic() >= self.deadline:
            raise TimeoutError("Agent deadline reached before dispatch.")
        if self.model_calls >= 16:
            raise ModelError("已达到模型调用预算，任务停止。")
        self.model_calls += 1
        await self.emit(
            "model",
            "正在请求模型。",
            usage={
                **self.usage,
                "model_calls": self.model_calls,
                "usage_reported_calls": self.usage_calls,
                "complete": False,
            },
        )
        # 单次期限独立于 HTTP 分段超时；网络失败不自动重发，以免重复计费。
        limited_by_total = False
        try:
            remaining = self.model.config.timeout
            if self.deadline is not None:
                total_remaining = self.deadline - monotonic()
                limited_by_total = total_remaining <= remaining
                remaining = min(remaining, total_remaining)
                if remaining <= 0:
                    raise TimeoutError
            async with asyncio.timeout(remaining):
                reply = await self.model.complete(messages, tools)
        except TimeoutError as exc:
            # 计时回调可能略早于时钟精度，按实际采用的期限分类；延迟处理也不能漏掉总期限。
            if limited_by_total or (self.deadline is not None and monotonic() >= self.deadline):
                raise
            raise ModelError("模型请求超时，可能已计费；没有自动重试。") from exc
        if reply.usage:
            self.usage_calls += 1
            if {
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
            } <= reply.usage.keys():
                self.usage_complete_calls += 1
            for key, value in reply.usage.items():
                self.usage[key] = self.usage.get(key, 0) + value
        await self.emit(
            "usage",
            "模型请求已完成。",
            usage={
                **self.usage,
                "model_calls": self.model_calls,
                "usage_reported_calls": self.usage_calls,
                "complete": self.usage_complete_calls == self.model_calls,
            },
        )
        return reply.message

    async def structured(self, messages, schema, *, results=None):
        for attempt in range(2):
            reply = await self.ask(messages)
            references = []
            try:
                if reply.get("tool_calls"):
                    raise ValueError
                content = json_content(reply.get("content") or "")
                if results is not None:
                    try:
                        candidate = json.loads(content)
                    except ValueError:
                        candidate = None
                    references = reference_diagnostics(candidate, results)
                draft = schema.model_validate_json(content)
                if results is not None:
                    # 结构、引用和数值共用一次修复；最终节点仍核对数据版本和全部约束。
                    validate_report(draft, results, log_validation=False)
                return draft
            except (ValidationError, ValueError, ReportError) as exc:
                issues = (
                    schema_diagnostics(exc, schema)
                    if isinstance(exc, ValidationError)
                    else [{"type": "report_constraint", "constraint": str(exc)}]
                    if isinstance(exc, ReportError)
                    else [{"type": "unexpected_tool_calls", "path": []}]
                )
                issues += references
                if self.repairs or attempt:
                    # 最终失败也保留安全字段诊断，避免修复失败后再次只剩笼统错误。
                    raise ModelError(
                        "模型结构或报告约束校验未通过，已用完一次修复预算。"
                        + json.dumps(issues, ensure_ascii=False)
                    ) from exc
                self.repairs += 1
                await self.emit("repair", "结构或报告约束不符合要求，进行一次受限修复。")
                messages = [
                    *messages,
                    # 上次输出只在内存中续接，帮助定位修复；不写入日志、事件或数据库。
                    {"role": "assistant", "content": reply.get("content") or ""},
                    {
                        "role": "user",
                        "content": "修正上次输出的结构与报告约束，引用必须从给定目录精确选择。只返回符合给定 JSON Schema 的对象，不调用工具，不输出 Markdown。已检测问题（无输入值）："
                        + json.dumps(
                            {
                                "issues": issues,
                                "guidance": repair_guidance(schema.__name__, issues),
                            },
                            ensure_ascii=False,
                        ),
                    },
                ]
        raise AssertionError("unreachable")

    async def check(self, state):
        from app.core.database import SessionLocal

        def verify():
            with SessionLocal() as db:
                self.executor.check_snapshot(db)

        await asyncio.to_thread(verify)
        await self.emit("check", "已确认本次任务的数据范围与版本。")
        return {"results": [], "calls": []}

    async def plan(self, state):
        # 索引/映射版本及文件哈希由执行器固定和校验；模型只需要可调用的数据源 ID。
        # 避免把内部索引 ID 当成 source_ids，减少无用上下文且不放宽数据范围。
        sources = source_context(self.run.snapshot)
        self.sources = sources
        schema = Plan.model_json_schema()
        schema["$defs"]["TaskRequirement"]["properties"]["source_id"]["anyOf"][0]["enum"] = [
            source["id"] for source in sources
        ]
        if not any(source["kind"] == "csv" and source["mapping_fields"] for source in sources):
            # 无可计算订单时移除无关指标格式，仍须把用户的指标请求列为缺资料的未知。
            requirement_schema = schema["$defs"]["TaskRequirement"]["properties"]
            requirement_schema["kind"]["enum"] = ["evidence", "other"]
            requirement_schema["metric"] = {"type": "null", "default": None}
            schema["$defs"].pop("MetricInput", None)
        prompts = [
            {"role": "system", "content": SYSTEM + "\n" + PLAN_RULES},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": self.run.question,
                        "sources": sources,
                        "format_example": {
                            "steps": [
                                "确认选中资料和字段。",
                                "计算所需指标或检索证据，说明资料不足。",
                            ],
                            "requirements": [
                                {
                                    "kind": "other",
                                    "description": "确认问题所需字段和日期范围。",
                                    "source_id": None,
                                    "metric": None,
                                }
                            ],
                        },
                        "schema": schema,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        plan = await self.structured(prompts, Plan)
        selected = {source["id"]: source for source in sources}
        for requirement in plan.requirements:
            if requirement.source_id and str(requirement.source_id) not in selected:
                raise ModelError("规划引用了未选中的资料，任务停止。")
            if requirement.kind == "metric" and requirement.source_id:
                source = selected[str(requirement.source_id)]
                if source["kind"] != "csv" or not source["mapping_fields"]:
                    raise ModelError("规划的指标来源必须是已映射 CSV，任务停止。")
        self.requirements = plan.requirements or [
            TaskRequirement(kind="other", description=step) for step in plan.steps
        ]
        await self.emit("plan", "分析计划已生成。", plan=plan.steps)
        return {
            "plan": plan.steps,
            "messages": [
                {"role": "system", "content": SYSTEM + "\n" + REASON_RULES},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"question": self.run.question, "sources": sources, "plan": plan.steps},
                        ensure_ascii=False,
                    ),
                },
            ],
        }

    async def reason(self, state):
        if self.model_calls >= 14:
            await self.emit("budget", "为报告生成与一次修复预留调用预算，使用现有结果并说明限制。")
            return {"calls": []}
        if len(state["results"]) >= 12:
            await self.emit("budget", "已达到工具预算，使用现有结果生成报告并说明限制。")
            return {"calls": []}
        messages = [
            *state["messages"],
            {
                "role": "user",
                "content": json.dumps(
                    {"task_state": task_state(self.requirements, state["results"], self.sources)},
                    ensure_ascii=False,
                ),
            },
        ]
        reply = await self.ask(messages, definitions(self.run.snapshot["sources"]))
        calls = reply.get("tool_calls") or []
        if len(calls) + len(state["results"]) > 12:
            raise ModelError("模型请求超过十二次工具预算，任务停止。")
        # 当前状态只发一次；下一轮重新生成，不把旧预算提醒累积进历史。
        return {"messages": [*state["messages"], reply], "calls": calls}

    async def tools(self, state):
        results, messages = list(state["results"]), list(state["messages"])
        for call in state["calls"]:
            function = call["function"]
            name = function["name"]
            # 先校验工具名，事件和日志不接受模型自造的名称或参数文本。
            if name not in {entry["function"]["name"] for entry in definitions()}:
                raise ModelError("模型请求了未允许的工具，任务停止。")
            await self.emit("tool_start", f"正在执行只读工具：{name}。")
            result = await asyncio.to_thread(
                self.executor.execute,
                name,
                function["arguments"],
                f"R{len(results) + 1}",
            )
            results.append(result)
            await self.emit("tool_end", f"工具已完成：{name}。", results=results)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(result_view(result), ensure_ascii=False),
                }
            )
        return {"results": results, "messages": messages, "calls": []}

    def report_prompts(self, state):
        facts, evidence = build_catalog(state["results"])
        metric_ids = [
            result["id"] for result in state["results"] if result["tool"] == "calculate_metric"
        ]
        chart_ids = [
            result["id"]
            for result in state["results"]
            if result["tool"] in ("calculate_metric", "group_by") and result["data"].get("groups")
        ]
        schema = ReportDraft.model_json_schema()
        # 每次独立生成 Schema，枚举只对本任务有效，服务端仍校验并发变化及真实引用。
        schema["$defs"]["FactReference"]["properties"]["fact_id"]["enum"] = list(facts)
        schema["$defs"]["Finding"]["properties"]["evidence_ids"]["items"]["enum"] = list(evidence)
        schema["properties"]["metric_ids"]["items"]["enum"] = metric_ids
        schema["$defs"]["ChartReference"]["properties"]["result_id"]["enum"] = chart_ids
        return [
            {"role": "system", "content": SYSTEM + "\n" + REPORT_RULES},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": self.run.question,
                        "results": [
                            result_view(result, for_report=True) for result in state["results"]
                        ],
                        "task_state": task_state(self.requirements, state["results"], self.sources),
                        "fact_catalog": list(facts.values()),
                        "evidence_catalog": list(evidence.values()),
                        "metric_catalog": [
                            result["id"]
                            for result in state["results"]
                            if result["tool"] == "calculate_metric"
                        ],
                        "chart_catalog": [
                            result["id"]
                            for result in state["results"]
                            if result["tool"] in ("calculate_metric", "group_by")
                            and result["data"].get("groups")
                        ],
                        "schema": schema,
                        "format_example": report_example(facts, metric_ids, chart_ids),
                    },
                    ensure_ascii=False,
                ),
            },
        ]

    async def report(self, state):
        await self.emit("report", "正在生成有证据约束的报告。")
        draft = await self.structured(
            self.report_prompts(state), ReportDraft, results=state["results"]
        )
        return {"draft": draft.model_dump()}

    async def validate(self, state):
        draft = ReportDraft.model_validate(state["draft"])
        try:
            report = validate_report(draft, state["results"])
        except ReportError as exc:
            if self.repairs:
                # ReportError 仅含服务端构造的字段路径与约束说明，不含模型原文。
                raise ModelError("报告证据或数值校验失败，已用完修复预算。" + str(exc)) from exc
            self.repairs += 1
            await self.emit("repair", "报告引用或数值约束不符，进行一次受限修复。")
            reply = await self.ask(
                [*self.report_prompts(state), {"role": "user", "content": str(exc)}]
            )
            try:
                draft = ReportDraft.model_validate_json(reply.get("content") or "")
                report = validate_report(draft, state["results"])
            except ValidationError as second:
                detail = json.dumps(schema_diagnostics(second, ReportDraft), ensure_ascii=False)
                raise ModelError("报告结构校验仍未通过，停止任务。" + detail) from second
            except ReportError as second:
                raise ModelError("报告校验仍未通过，停止任务。" + str(second)) from second
        await self.check(state)
        await self.emit("validated", "报告结构、数值来源和引用已校验；语义仍需人工核对。")
        return {"report": report}

    async def run_graph(self):
        # 关闭外部追踪，业务数据与 reasoning_content 不上传到第三方追踪服务。
        with tracing_context(enabled=False):
            return await self.graph.ainvoke({}, {"recursion_limit": 40})
