import json
import logging
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from sqlalchemy import text

REQUESTS = Counter(
    "inferenceiq_http_requests_total",
    "HTTP requests handled by InferenceIQ",
    ("method", "route", "status"),
)
LATENCY = Histogram(
    "inferenceiq_http_request_duration_seconds",
    "InferenceIQ HTTP request duration",
    ("method", "route"),
)
IN_FLIGHT = Gauge(
    "inferenceiq_http_requests_in_flight",
    "InferenceIQ HTTP requests currently in flight",
)


def configure_operations(app: FastAPI) -> None:
    logger = logging.getLogger("inferenceiq.requests")
    logger.setLevel(logging.INFO)

    @app.middleware("http")
    async def operational_middleware(request: Request, call_next):
        supplied_request_id = request.headers.get("x-request-id", "")
        request_id = (
            supplied_request_id
            if supplied_request_id and len(supplied_request_id) <= 128
            else f"req_{uuid4().hex}"
        )
        request.state.request_id = request_id
        started_at = perf_counter()
        status_code = 500
        IN_FLIGHT.inc()
        try:
            response = await call_next(request)
            status_code = response.status_code
        finally:
            duration = perf_counter() - started_at
            route = request.scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            REQUESTS.labels(request.method, route_path, str(status_code)).inc()
            LATENCY.labels(request.method, route_path).observe(duration)
            IN_FLIGHT.dec()
            logger.info(
                json.dumps(
                    {
                        "event": "http_request",
                        "request_id": request_id,
                        "method": request.method,
                        "route": route_path,
                        "status": status_code,
                        "duration_ms": round(duration * 1000, 2),
                    },
                    separators=(",", ":"),
                )
            )
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/ready", tags=["operations"])
    async def readiness(request: Request) -> dict[str, str]:
        async with request.app.state.database.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return {"status": "ready"}
