from fastapi import FastAPI, HTTPException, Request, Response, status
from pydantic import ValidationError

from sensor_readings.models import Reading
from sensor_readings.store import ReadingStore

READING_BODY = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": Reading.model_json_schema()}},
    }
}


def create_app() -> FastAPI:
    app = FastAPI(title="Sensor readings")
    store = ReadingStore()

    @app.post(
        "/readings", status_code=status.HTTP_201_CREATED, openapi_extra=READING_BODY
    )
    async def post_reading(request: Request, response: Response) -> dict[str, str]:
        try:
            reading = Reading.model_validate_json(await request.body())
        except ValidationError as error:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                error.errors(
                    include_url=False, include_context=False, include_input=False
                ),
            ) from None
        outcome = store.add(reading)
        if outcome == "conflict":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"event_id {reading.event_id!r} already exists with different data",
            )
        if outcome == "duplicate":
            response.status_code = status.HTTP_200_OK
        return {"status": outcome}

    @app.get("/sensors/{sensor_id:path}/latest")
    async def get_latest(sensor_id: str) -> Reading:
        reading = store.latest_for(sensor_id)
        if reading is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"no readings for sensor {sensor_id!r}"
            )
        return reading

    return app


app = create_app()
