from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_serializer

from app.core.time import serialize_api_datetime


class ApiSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("*", when_used="json")
    def serialize_datetime_fields(self, value: object) -> object:
        if isinstance(value, datetime):
            return serialize_api_datetime(value)
        return value