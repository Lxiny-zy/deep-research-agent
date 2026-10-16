"""HTTP error metadata while preserving legacy FastAPI detail payloads."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

_CODES = {
    400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
    409: "conflict", 413: "payload_too_large", 422: "invalid_request",
    429: "rate_limited", 503: "service_unavailable",
}


def install_error_handlers(app: FastAPI) -> None:
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        payload: dict[str, Any] = {
            "detail": exc.detail,
            "code": _CODES.get(exc.status_code, "http_error"),
            "request_id": getattr(request.state, "request_id", None),
            "retryable": exc.status_code in {408, 429, 502, 503, 504},
        }
        if isinstance(exc.detail, dict):
            payload["code"] = exc.detail.get("code", payload["code"])
            if isinstance(exc.detail.get("message"), str):
                payload["message"] = exc.detail["message"]
        elif isinstance(exc.detail, str):
            payload["message"] = exc.detail
        return JSONResponse(
            jsonable_encoder(payload), status_code=exc.status_code, headers=exc.headers,
        )

    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Do not echo request input (which can contain provider credentials or
        # entire uploaded documents) in validation error responses.
        detail = [
            {key: error[key] for key in ("loc", "msg", "type") if key in error}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content=jsonable_encoder({
            "detail": detail, "code": "validation_error", "retryable": False,
            "request_id": getattr(request.state, "request_id", None),
        }))

    app.add_exception_handler(HTTPException, http_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error)  # type: ignore[arg-type]
