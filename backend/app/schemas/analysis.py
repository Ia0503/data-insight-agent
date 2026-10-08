from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MappingInput(Input):
    # CSV 列标识和原始筛选值属于原文，不能静默去掉首尾空格。
    model_config = ConfigDict(str_strip_whitespace=False)
    fields: dict[str, str] = Field(min_length=1, max_length=20)


class Filter(Input):
    model_config = ConfigDict(str_strip_whitespace=False)
    field: str = Field(min_length=1, max_length=120)
    operator: Literal["eq", "ne", "contains", "gt", "gte", "lt", "lte"] = "eq"
    value: str = Field(max_length=200)


class ToolInput(Input):
    model_config = ConfigDict(str_strip_whitespace=False)
    tool: Literal["get_schema", "filter_data", "group_by"]
    filters: list[Filter] = Field(default_factory=list, max_length=10)
    group: str | None = Field(default=None, max_length=120)
    value: str | None = Field(default=None, max_length=120)
    aggregation: Literal["count", "sum", "mean", "min", "max"] = "count"
    limit: int = Field(default=50, ge=1, le=100)


class MetricInput(Input):
    metric: Literal["paid_sales", "net_sales", "paid_orders", "refund_rate"] = "net_sales"
    start: date = Field(
        ge=date(1900, 1, 1),
        le=date(2100, 12, 31),
        description="当期开始日期。比较两个相邻自然月时只填写当期月份，不把当期与基期合并为一个区间。",
    )
    end: date = Field(
        ge=date(1900, 1, 1),
        le=date(2100, 12, 31),
        description="当期结束日期，与 start 共同限定当期统计范围；基期由 compare_previous 自动计算。",
    )
    region: str | None = Field(default=None, max_length=120)
    product: str | None = Field(default=None, max_length=120)
    group: Literal["month", "region", "product"] | None = None
    compare_previous: bool = Field(
        default=False,
        description="需要基期比较时为 true；完整自然月比较上个自然月，其他范围比较前一个同长度区间。无需另算基期。",
    )

    @model_validator(mode="after")
    def period(self):
        if self.end < self.start or (self.end - self.start).days > 3660:
            raise ValueError("日期范围必须有序且不超过十年。")
        return self


class IndexInput(Input):
    model_config = ConfigDict(str_strip_whitespace=False)
    text_field: str | None = Field(default=None, max_length=120)
    record_id_field: str | None = Field(default=None, max_length=120)
    force: bool = False


class SearchInput(Input):
    query: str = Field(min_length=1, max_length=1000)
    source_ids: list[UUID] = Field(default_factory=list, max_length=20)
    top_k: int = Field(default=5, ge=1, le=20)
    min_similarity: float = Field(default=0.35, ge=-1, le=1)
