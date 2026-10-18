from typing import Literal

from sensor_readings.models import Reading

type Outcome = Literal["accepted", "duplicate", "conflict"]


class ReadingStore:
    def __init__(self) -> None:
        self._events: dict[str, Reading] = {}
        self._latest: dict[str, Reading] = {}

    def add(self, reading: Reading) -> Outcome:
        known = self._events.get(reading.event_id)
        if known is not None:
            return "duplicate" if known.same_as(reading) else "conflict"
        self._events[reading.event_id] = reading
        current = self._latest.get(reading.sensor_id)
        if current is None or reading.moment > current.moment:
            self._latest[reading.sensor_id] = reading
        return "accepted"

    def latest_for(self, sensor_id: str) -> Reading | None:
        return self._latest.get(sensor_id)
