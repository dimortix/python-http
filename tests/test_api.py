import json

import httpx2
import pytest
from fastapi.testclient import TestClient

from sensor_readings.main import create_app

SENSOR = "room-01-temperature"
OTHER_SENSOR = "room-02-temperature"
TEN_AM = "2026-10-12T10:00:00Z"


def reading(
    event_id: object = "event-001",
    value: object = 22.4,
    observed_at: object = TEN_AM,
    **extra: object,
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "sensor_id": SENSOR,
        "value": value,
        "observed_at": observed_at,
        **extra,
    }


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def accept(client: TestClient, body: dict[str, object]) -> None:
    response = client.post("/readings", json=body)
    assert (response.status_code, response.json()) == (201, {"status": "accepted"})


def latest(client: TestClient, sensor_id: str = SENSOR) -> httpx2.Response:
    return client.get(f"/sensors/{sensor_id}/latest")


def test_scenario_from_the_task(client: TestClient) -> None:
    first = reading("event-001", 22.4, TEN_AM)

    accept(client, first)
    assert latest(client).json() == first

    response = client.post("/readings", json=first)
    assert (response.status_code, response.json()) == (200, {"status": "duplicate"})
    assert latest(client).json() == first

    accept(client, reading("event-002", 21.8, "2026-10-12T09:59:00Z"))
    assert latest(client).json() == first

    response = client.post("/readings", json=reading("event-001", 25.0, TEN_AM))
    assert response.status_code == 409
    assert latest(client).json() == first

    third = reading("event-003", 23.0, "2026-10-12T10:01:00Z")
    accept(client, third)
    assert latest(client).json() == third


def test_accepted_reading_is_returned_as_latest(client: TestClient) -> None:
    accept(client, reading())

    response = latest(client)

    assert response.status_code == 200
    assert response.json() == reading()


def test_sensor_without_readings_returns_404(client: TestClient) -> None:
    accept(client, reading())

    response = latest(client, OTHER_SENSOR)

    assert response.status_code == 404
    assert OTHER_SENSOR in response.json()["detail"]


def test_sensor_id_with_slash_can_be_read(client: TestClient) -> None:
    body = {**reading(), "sensor_id": "building/room-01"}
    accept(client, body)

    assert client.get("/sensors/building%2Froom-01/latest").json() == body
    assert client.get("/sensors/building/room-01/latest").json() == body


@pytest.mark.parametrize(
    ("original", "repeat"),
    [
        (reading(value=22), reading(value=22.0)),
        (reading(value=22.0), reading(value=22)),
        (
            reading(observed_at="2026-10-12T10:00:00Z"),
            reading(observed_at="2026-10-12T10:00:00.000Z"),
        ),
        (
            reading(observed_at="2026-10-12T10:00:00.5Z"),
            reading(observed_at="2026-10-12T10:00:00.500000000Z"),
        ),
    ],
    ids=["int-then-float", "float-then-int", "zero-fraction", "fraction-length"],
)
def test_equal_repeat_is_duplicate_and_keeps_state(
    client: TestClient, original: dict[str, object], repeat: dict[str, object]
) -> None:
    accept(client, original)

    response = client.post("/readings", json=repeat)

    assert (response.status_code, response.json()) == (200, {"status": "duplicate"})
    assert latest(client).json() == original


@pytest.mark.parametrize(
    "changes",
    [
        {"value": 25.0},
        {"observed_at": "2026-10-12T10:00:00.001Z"},
        {"sensor_id": OTHER_SENSOR},
    ],
    ids=["value", "observed_at", "sensor_id"],
)
def test_repeat_with_different_data_is_conflict_and_keeps_state(
    client: TestClient, changes: dict[str, object]
) -> None:
    accept(client, reading())

    response = client.post("/readings", json={**reading(), **changes})

    assert response.status_code == 409
    assert "event-001" in response.json()["detail"]
    assert latest(client).json() == reading()
    assert latest(client, OTHER_SENSOR).status_code == 404
    assert client.post("/readings", json=reading()).status_code == 200


def test_older_reading_is_stored_but_does_not_replace_latest(
    client: TestClient,
) -> None:
    newer = reading("event-001", 22.4, TEN_AM)
    older = reading("event-002", 21.8, "2026-10-12T09:59:59.999Z")
    accept(client, newer)

    accept(client, older)
    assert latest(client).json() == newer

    assert client.post("/readings", json=older).status_code == 200
    assert client.post("/readings", json={**older, "value": 1.0}).status_code == 409
    assert latest(client).json() == newer


def test_newer_reading_replaces_older_latest(client: TestClient) -> None:
    older = reading("event-002", 21.8, "2026-10-12T09:59:00Z")
    newer = reading("event-001", 22.4, TEN_AM)
    accept(client, older)

    accept(client, newer)

    assert latest(client).json() == newer


def test_same_moment_keeps_first_accepted_reading(client: TestClient) -> None:
    first = reading("event-001", 22.4, TEN_AM)
    second = reading("event-002", 23.5, "2026-10-12T10:00:00.000Z")
    accept(client, first)

    accept(client, second)

    assert latest(client).json() == first


def test_fractional_seconds_are_compared_exactly(client: TestClient) -> None:
    base = reading("event-001", 1.0, "2026-10-12T10:00:00.1Z")
    accept(client, base)
    accept(client, reading("event-002", 2.0, "2026-10-12T10:00:00.09999999Z"))
    assert latest(client).json() == base

    newest = reading("event-003", 3.0, "2026-10-12T10:00:00.1000000001Z")
    accept(client, newest)
    assert latest(client).json() == newest


def test_reading_is_accepted_without_json_content_type(client: TestClient) -> None:
    response = client.post(
        "/readings",
        content=json.dumps(reading()),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 201


def as_body(payload: object) -> bytes:
    return payload if isinstance(payload, bytes) else json.dumps(payload).encode()


def with_raw_value(literal: str) -> bytes:
    body = json.dumps(reading("event-bad", "VALUE"))
    return body.replace('"VALUE"', literal).encode()


def without(field: str) -> dict[str, object]:
    return {key: value for key, value in reading("event-bad").items() if key != field}


def bad_time(observed_at: object) -> dict[str, object]:
    return reading("event-bad", observed_at=observed_at)


INVALID_BODIES = {
    "missing event_id": without("event_id"),
    "missing sensor_id": without("sensor_id"),
    "missing value": without("value"),
    "missing observed_at": without("observed_at"),
    "unknown field": reading("event-bad", unit="celsius"),
    "empty event_id": reading(""),
    "empty sensor_id": {**reading("event-bad"), "sensor_id": ""},
    "numeric event_id": {**reading("event-bad"), "event_id": 1},
    "null sensor_id": {**reading("event-bad"), "sensor_id": None},
    "value as string": reading("event-bad", "22.4"),
    "value as boolean": reading("event-bad", True),
    "value null": reading("event-bad", None),
    "value as list": reading("event-bad", [22.4]),
    "value NaN": with_raw_value("NaN"),
    "value Infinity": with_raw_value("Infinity"),
    "value overflows to infinity": with_raw_value("1e400"),
    "observed_at without Z": bad_time("2026-10-12T10:00:00"),
    "observed_at with offset": bad_time("2026-10-12T10:00:00+00:00"),
    "observed_at lowercase z": bad_time("2026-10-12T10:00:00z"),
    "observed_at without seconds": bad_time("2026-10-12T10:00Z"),
    "observed_at empty fraction": bad_time("2026-10-12T10:00:00.Z"),
    "observed_at with space": bad_time("2026-10-12 10:00:00Z"),
    "observed_at date only": bad_time("2026-10-12"),
    "observed_at impossible day": bad_time("2026-02-30T10:00:00Z"),
    "observed_at hour 24": bad_time("2026-10-12T24:00:00Z"),
    "observed_at non-ASCII digits": bad_time(
        "2026-10-12T10:00:00.\N{ARABIC-INDIC DIGIT FIVE}Z"
    ),
    "observed_at as number": bad_time(1760263200),
    "body is a list": [reading("event-bad")],
    "body is a string": "event-bad",
    "body is not JSON": b"event_id=event-bad",
    "empty body": b"",
}


@pytest.mark.parametrize("payload", INVALID_BODIES.values(), ids=INVALID_BODIES.keys())
def test_invalid_reading_is_rejected_without_changing_state(
    client: TestClient, payload: object
) -> None:
    accept(client, reading())

    response = client.post(
        "/readings",
        content=as_body(payload),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail
    assert all(error["msg"] for error in detail)
    assert latest(client).json() == reading()
    accept(client, reading("event-bad", 30.0, "2026-10-12T11:00:00Z"))
