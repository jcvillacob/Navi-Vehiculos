from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, Response

from app.core.config import settings

router = APIRouter(include_in_schema=False)

_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


async def _forward_to_frontend(request: Request, full_path: str = "") -> Response:
    base = settings.frontend_upstream.rstrip("/")
    target = f"{base}/{full_path}" if full_path else f"{base}/"
    forward_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _HOP_BY_HOP_HEADERS and key.lower() != "host"
    }

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        upstream_response = await client.request(
            method=request.method,
            url=target,
            params=request.query_params,
            headers=forward_headers,
            content=await request.body(),
        )

    response_headers = {
        key: value
        for key, value in upstream_response.headers.items()
        if key.lower() not in _HOP_BY_HOP_HEADERS
    }

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=upstream_response.headers.get("content-type"),
    )


@router.api_route(
    "/",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy_root(request: Request) -> Response:
    return await _forward_to_frontend(request)


@router.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy_catch_all(request: Request, full_path: str) -> Response:
    return await _forward_to_frontend(request, full_path)
