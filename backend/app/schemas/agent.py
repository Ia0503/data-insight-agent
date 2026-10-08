from typing import Annotated, Literal
from uuid import UUID

from app.schemas.analysis import Input, MetricInput
from pydantic import Field, StringConstraints, field_validator, model_validator


class RunInput(Input):
    question: str = Field(min_length=1, max_length=2000)
    source_ids: list[UUID] = Field(min_length=1, max_length=20)
    request_id: UUID

    @field_validator("source_ids")
    @classmethod
    def unique_sources(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("数据源不能重复。")
        return value


ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]


class TaskRequirement(Input):
    kind: Literal["metric", "evidence", "other"]
    description: ShortText
    source_id: UUID | None = None
    metric: MetricInput | None = None

    @model_validator(mode="after")
    def metric_target(self):
        if (self.kind == "metric") != (self.metric is not None):
            raise ValueError("metric 需求须给出明确统计范围，其他需求的 metric 必须为 null。")
        if self.kind == "metric" and self.source_id is None:
            raise ValueError("metric 需求须选择已映射的数据源。")
        return self


class Plan(Input):
    steps: list[ShortText] = Field(min_length=1, max_length=8)
    # 仅用于任务内的模型上下文，不改变网页的步骤数组或持久化报告结构。
    requirements: list[TaskRequirement] = Field(default_factory=list, max_length=8)

    @field_validator("steps")
    @classmethod
    def bounded_steps(cls, values):
        if any(not value.strip() or len(value) > 240 for value in values):
            raise ValueError("计划步骤不能为空且不能超过240字符。")
        return values


class Finding(Input):
    kind: Literal["inference", "unknown"]
    text: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def evidence_required(self):
        if self.kind != "unknown" and not self.evidence_ids:
            raise ValueError("事实和推断必须引用证据。")
        return self


class FactReference(Input):
    kind: Literal["fact"]
    fact_id: str = Field(min_length=1, max_length=160)


class ReportDraft(Input):
    title: str = Field(min_length=1, max_length=120)
    # 摘要与事实文字由服务端生成，模型不能通过自由摘要绕开事实选择约束。
    summary: Literal[""] = ""
    findings: list[FactReference | Finding] = Field(min_length=1, max_length=12)
    recommendations: list[str] = Field(default_factory=list, max_length=8)
    limitations: list[str] = Field(min_length=1, max_length=8)
    metric_ids: list[str] = Field(default_factory=list, max_length=12)
    chart_specs: list["ChartReference"] = Field(default_factory=list, max_length=4)

    @field_validator("recommendations", "limitations")
    @classmethod
    def bounded_text(cls, values):
        if any(not value.strip() or len(value) > 1000 for value in values):
            raise ValueError("建议和限制条目不能为空且不能超过1000字符。")
        return values


class ChartReference(Input):
    kind: Literal["bar", "line"] = "bar"
    title: str = Field(min_length=1, max_length=120)
    result_id: str = Field(min_length=1, max_length=30)


ReportDraft.model_rebuild()
