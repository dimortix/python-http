# Sensor readings service

A small HTTP service that accepts room sensor readings and returns the latest reading for each sensor. It keeps everything in the memory of one process, so data is lost on restart.

Stack: Python 3.15, FastAPI 0.146, Pydantic 2.14, uvicorn, pytest 9, ruff, mypy.

## Architecture

![Request flow: main.py validates the body with the Reading model, passes a valid reading to ReadingStore.add, and reads the latest reading through latest_for](docs/architecture.svg)

POST validates the raw body against `Reading` first, so an invalid request never reaches the store. A valid reading goes to `ReadingStore.add`, which looks up its `event_id` in `_events` to tell a new event from a duplicate or a conflict. It replaces the sensor's entry in `_latest` only when the new reading is newer. GET reads `_latest` through `latest_for`.

Dependencies point one way: `main` uses `store` and `models`, `store` uses `models`, and `models` knows nothing about HTTP or storage. Each module has one job, so a change to validation, to storage or to the HTTP layer stays in one file.

The code lives in `src/sensor_readings/` (the src layout from the Python Packaging User Guide), the tests in `tests/test_api.py`, and this diagram in `docs/architecture.svg`.

## Setup

You need [uv](https://docs.astral.sh/uv/) (tested with 0.13.1). If Python 3.15 is missing, uv downloads it.

```bash
uv sync
```

Without uv, any Python 3.15 with pip 25.1 or newer works:

```bash
python3.15 -m venv .venv
source .venv/bin/activate
pip install -e . --group dev
```

With pip the versions are not pinned. `uv.lock` pins the exact versions I tested. In a pip environment, run the commands below without `uv run`.

## Run

```bash
uv run uvicorn sensor_readings.main:app
```

The service listens on http://127.0.0.1:8000. FastAPI also serves interactive docs at http://127.0.0.1:8000/docs.

## Tests and checks

```bash
uv run pytest
uv run ruff check
uv run ruff format --check
uv run mypy src tests
```

pytest turns warnings into errors, and mypy runs in strict mode. All four commands pass.

The 47 tests cover the five-step scenario from the task, normal accept and read, exact repeats (including 22 vs 22.0 and `.000Z` vs no fraction), conflicts on value, time and sensor, readings with an older `observed_at`, equal moments, sub-microsecond ordering, 404 for an unknown sensor, and 31 invalid bodies. Every setup request in a test asserts its own 201, so a test can't pass because its setup quietly failed. After each 422 the test checks that the latest reading did not change and that the rejected `event_id` can still be accepted, which means nothing was saved halfway.

## Example requests

Accept a reading:

```bash
curl -i -X POST http://127.0.0.1:8000/readings \
  -H 'Content-Type: application/json' \
  -d '{"event_id": "event-001", "sensor_id": "room-01-temperature", "value": 22.4, "observed_at": "2026-10-12T10:00:00Z"}'
```

```
HTTP/1.1 201 Created
{"status":"accepted"}
```

Sending the same body again returns `200` and `{"status":"duplicate"}`. Sending `event-001` with a different value, sensor or time returns `409`:

```json
{"detail": "event_id 'event-001' already exists with different data"}
```

Read the latest reading:

```bash
curl -i http://127.0.0.1:8000/sensors/room-01-temperature/latest
```

```
HTTP/1.1 200 OK
{"event_id":"event-001","sensor_id":"room-01-temperature","value":22.4,"observed_at":"2026-10-12T10:00:00Z"}
```

A sensor with no readings returns `404` with `{"detail": "no readings for sensor 'room-02-temperature'"}`.

An invalid body returns `422` and lists every problem it found:

```bash
curl -i -X POST http://127.0.0.1:8000/readings \
  -H 'Content-Type: application/json' \
  -d '{"event_id": "event-004", "sensor_id": "room-01-temperature", "value": "23", "observed_at": "2026-10-12T10:02:00+00:00", "unit": "C"}'
```

```json
{"detail": [
  {"type": "extra_forbidden", "loc": ["unit"], "msg": "Extra inputs are not permitted"},
  {"type": "float_type", "loc": ["value"], "msg": "Input should be a valid number"},
  {"type": "value_error", "loc": ["observed_at"], "msg": "Value error, must be a UTC date and time like 2026-10-12T10:00:00Z, fractional seconds are optional"}
]}
```

## How it works

`Reading` is a Pydantic model in strict mode that forbids unknown fields. Strict mode rejects strings, booleans and `null` for `value`. Integers are accepted and stored as floats, so 22 and 22.0 are the same value. `allow_inf_nan=False` rejects `NaN`, `Infinity` and numbers too large for a float, such as `1e400`.

`observed_at` has to match `YYYY-MM-DDTHH:MM:SS[.fraction]Z` with an hour from 00 to 23. Then `datetime.fromisoformat` checks that the date exists, so `2026-02-30T10:00:00Z` is rejected. For comparison, `parse_moment` splits the timestamp into whole seconds and an exact `Decimal` fraction. `10:00:00Z` and `10:00:00.000Z` become the same moment, and fractions longer than six digits are still ordered correctly. A plain `datetime` stops at microseconds and would lose them.

The POST handler reads the raw body and validates it with `Reading.model_validate_json`. FastAPI's usual body parameter would cause two problems here. Since version 0.132 FastAPI rejects JSON that arrives without `Content-Type: application/json`, so `curl -d` with a valid reading would get a 422. And for `NaN` or `1e400` its default 422 handler tries to echo the value back and fails with a 500, because `NaN` can't be written as JSON. Parsing the body directly avoids both. `openapi_extra` puts the request schema back into `/docs`.

`ReadingStore` keeps two private dicts. One maps every accepted `event_id` to its reading, older ones included, so repeats are found across all sensors. The other maps `sensor_id` to the reading with the greatest `observed_at`. A new reading replaces it only when it is strictly newer, so with equal times the first accepted reading stays. Validation finishes before the store is touched, so a rejected request changes nothing.

The route is `/sensors/{sensor_id:path}/latest`, so a sensor id that contains `/` can still be read, as `building/room-01` or URL-encoded as `building%2Froom-01`.

## Time spent

About 1 hour.

## Unfinished parts and limitations

Everything the task asks for is done. Outside its scope I did not add persistence, a request size limit or support for several worker processes.

- State lives in one process. Several uvicorn workers would each keep their own data.
- The handlers are `async` and never `await` between the repeat check and the write, so two requests can't interleave there on one event loop. Plain `def` handlers would run in a thread pool and the store would need a lock.
- Values are compared as 64-bit floats, which is what RFC 8259 calls the interoperable range for JSON numbers. Numbers that map to the same float count as the same value: 9007199254740993 and 9007199254740992, 0.1 and 0.10000000000000001, 1e-400 and 0. Finite numbers beyond the float range, like `1e400` or a 1 followed by 400 zeros, get a 422 that says "Input should be a finite number". An integer comes back as a float (22 becomes 22.0).
- Leap seconds such as `23:59:60Z` are rejected because Python's `datetime` can't represent them.
- Ids made only of whitespace, such as `" "`, are accepted. The task asks only for non-empty strings.
- If a JSON object repeats a key, the last value wins. RFC 8259 leaves that case to the parser.
- Memory grows with every accepted event, and nothing is ever evicted.

## AI usage

I built this with Claude Code on the Claude Opus 5.5 model, using my own agent setup. That setup assigns a tool or a rule to each stage of development.

1. Planning. In Claude Code's plan mode the agent read the task and wrote a plan covering the stack, validation rules, storage and the test list. I approved the plan and changed the Python version to 3.15 before any code was written.
2. Rules. My global `CLAUDE.md` maps kinds of work to skills (bug diagnosis, test-first work, code review, current docs, text for people) and says when to start subagents. The ponytail plugin keeps the code small: standard library and existing dependencies first, no abstractions the task doesn't need, and a test for every piece of non-trivial logic. I also asked for no comments in the code.
3. Current docs. The context7 MCP server fetches current library documentation instead of relying on the model's training data. That is how the FastAPI 0.132 `Content-Type` change came up. The agent also checked library behavior with throwaway scripts, for example how Pydantic parses `NaN` and `1e400`, and that Python 3.15 accepts `24:00:00` and turns it into midnight of the next day. A test caught that last case, and the hour is now limited to 00 to 23.
4. Review. The code-review skill ran two subagents in parallel. One checked the code against PEP 8, SOLID and Fowler's list of code smells. The other went through the task line by line and sent about 150 edge-case requests to the running server. Neither found a deviation from the task. After the review I moved timestamp parsing into one function, made the store's dicts private behind `latest_for`, removed a store parameter that only the tests used, made every setup request in the tests assert its 201, and expanded the limitations above.
5. Writing. This README went through a humanizer skill based on Wikipedia's "Signs of AI writing" list. The architecture diagram is hand-written SVG made with a diagramming skill, and I rendered it to check that every label stays readable.

To check the result I:

- ran pytest, ruff and mypy as listed above;
- started the server and went through the task's five-step scenario with curl, plus a 422, a 404 and a request sent with `curl -d` and no JSON header;
- broke the code on purpose in six ways (replacing latest on an equal time, allowing unknown fields, comparing repeats without normalizing them, not storing older events, picking latest by arrival order, ignoring fractional seconds) and confirmed that tests fail each time;
- installed the project with plain pip in a clean virtualenv and ran the tests there.
