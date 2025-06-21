from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from contextlib import asynccontextmanager
from starlette.types import Receive, Scope, Send
from starlette.responses import StreamingResponse, Response, PlainTextResponse
from starlette.requests import Request
from uuid import uuid4, UUID
import anyio
from anyio.streams.memory import (
    MemoryObjectReceiveStream,
    MemoryObjectSendStream,
)
import mcp.types as types
import json


class NDJSONResponse(StreamingResponse):
    """Streaming response for newline delimited JSON."""

    def __init__(self, content):
        super().__init__(content, media_type="application/x-ndjson")


class HttpStreamServerTransport:
    """Simple HTTP streaming transport using newline delimited JSON."""

    def __init__(self, endpoint: str) -> None:
        self._endpoint = endpoint
        self._read_stream_writers: dict[UUID, MemoryObjectSendStream] = {}

    async def handle_post_message(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        request = Request(scope, receive)
        session_param = request.query_params.get("session_id")
        if not session_param:
            await Response(status_code=400)(scope, receive, send)
            return
        session_id = UUID(session_param)
        writer = self._read_stream_writers.get(session_id)
        if writer is None:
            await Response(status_code=404)(scope, receive, send)
            return
        data = await request.json()
        # TODO: parse JSONRPCMessage correctly when mcp.types is available
        await writer.send(types.JSONRPCMessage(**data))
        await Response(status_code=204)(scope, receive, send)

    @asynccontextmanager
    async def connect_stream(
        self, scope: Scope, receive: Receive, send: Send
    ) -> tuple[
        MemoryObjectReceiveStream[types.JSONRPCMessage | Exception],
        MemoryObjectSendStream[types.JSONRPCMessage],
    ]:
        if scope["type"] != "http":
            raise ValueError("connect_stream can only handle HTTP requests")

        read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
        write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

        session_id = uuid4()
        session_uri = f"{self._endpoint}?session_id={session_id.hex}"
        self._read_stream_writers[session_id] = read_stream_writer

        async def iter_content():
            yield json.dumps({"endpoint": session_uri}) + "\n"
            async for message in write_stream_reader:
                yield (
                    json.dumps(
                        message.model_dump(by_alias=True, exclude_none=True)
                    )
                    + "\n"
                )

        response = NDJSONResponse(iter_content())
        await response(scope, receive, send)
        try:
            yield (read_stream, write_stream)
        finally:
            self._read_stream_writers.pop(session_id, None)


def create_http_stream_server(mcp: FastMCP) -> Starlette:
    """Create a Starlette app that handles HTTP streaming connections."""

    transport: HttpStreamServerTransport | None = None

    async def handle_stream(request: Request):
        nonlocal transport
        if transport is None:
            server_url = f"{request.url.scheme}://{request.url.netloc}"
            endpoint = f"{server_url}/http/messages/"
            transport = HttpStreamServerTransport(endpoint)

        async with transport.connect_stream(
            request.scope, request.receive, request._send
        ) as streams:
            await mcp._mcp_server.run(
                streams[0], streams[1], mcp._mcp_server.create_initialization_options()
            )

    async def handle_messages(scope: Scope, receive: Receive, send: Send):
        """Handle POST requests once the transport is initialized."""
        if transport is None:
            response = PlainTextResponse(
                "HTTP streaming transport not initialized", status_code=503
            )
            await response(scope, receive, send)
        else:
            await transport.handle_post_message(scope, receive, send)

    routes = [
        Route("/stream/", endpoint=handle_stream),
        Mount("/messages/", app=handle_messages),
    ]

    return Starlette(routes=routes)
