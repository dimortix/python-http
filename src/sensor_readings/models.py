import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

UTC_TIMESTAMP_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T([01]\d|2[0-3]):\d{2}:\d{2}(\.\d+)?Z", re.ASCII
)

type Moment = tuple[datetime, Decimal]


def parse_moment(observed_at: str) -> Moment:
    return datetime.fromisoformat(observed_at[:19]), Decimal("0" + observed_at[19:-1])


def check_utc_timestamp(value: str) -> str:
    if not UTC_TIMESTAMP_PATTERN.fullmatch(value):
        raise ValueError(
            "must be a UTC date and time like 2026-10-12T10:00:00Z, "
            "fractional seconds are optional"
        )
    try:
        parse_moment(value)
    except ValueError as error:
        raise ValueError(f"is not a valid date and time: {error}") from None
    return value


class Reading(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    event_id: str = Field(min_length=1)
    sensor_id: str = Field(min_length=1)
    value: float = Field(allow_inf_nan=False)
    observed_at: Annotated[str, AfterValidator(check_utc_timestamp)]

    @property
    def moment(self) -> Moment:
        return parse_moment(self.observed_at)

    def same_as(self, other: Reading) -> bool:
        mine = (self.sensor_id, self.value, self.moment)
        return mine == (other.sensor_id, other.value, other.moment)
