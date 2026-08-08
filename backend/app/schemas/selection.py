from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


SelectionMode = Literal["ids", "all", "filter"]


class SelectionSpec(BaseModel):
    """Explicit, reusable resource selection submitted by Agent clients."""

    mode: SelectionMode
    ids: list[int] = Field(default_factory=list)
    filter: dict[str, object] = Field(default_factory=dict)
    exclude_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_mode_inputs(self) -> "SelectionSpec":
        _validate_positive_unique_ids(self.ids, field_name="ids")
        _validate_positive_unique_ids(self.exclude_ids, field_name="exclude_ids")
        if self.mode == "ids":
            if not self.ids:
                raise ValueError("ids 选择模式必须至少提供一个 ID")
            if self.filter:
                raise ValueError("ids 选择模式不能提供 filter")
        elif self.mode == "all":
            if self.ids or self.filter:
                raise ValueError("all 选择模式不能提供 ids 或 filter")
        else:
            if self.ids:
                raise ValueError("filter 选择模式不能提供 ids")
            if not self.filter:
                raise ValueError("filter 选择模式必须提供至少一个筛选条件")
        return self


def _validate_positive_unique_ids(values: list[int], *, field_name: str) -> None:
    if any(value < 1 for value in values):
        raise ValueError(f"{field_name} 必须是正整数")
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} 不能包含重复 ID")


__all__ = ["SelectionMode", "SelectionSpec"]
