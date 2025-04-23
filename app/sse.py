from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport as BaseSseServerTransport
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from contextlib import asynccontextmanager
from starlette.types import Receive, Scope, Send
from uuid import uuid4
import anyio
from sse_starlette import EventSourceResponse
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
import mcp.types as types


class CustomSseServerTransport(BaseSseServerTransport):
    """Custom SSE transport that doesn't URL encode the full endpoint URL"""

    @asynccontextmanager
    async def connect_sse(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            raise ValueError("connect_sse can only handle HTTP requests")

        # Create streams with proper types
        read_stream_writer: MemoryObjectSendStream[types.JSONRPCMessage | Exception]
        read_stream: MemoryObjectReceiveStream[types.JSONRPCMessage | Exception]
        write_stream: MemoryObjectSendStream[types.JSONRPCMessage]
        write_stream_reader: MemoryObjectReceiveStream[types.JSONRPCMessage]

        # Create the stream pairs
        read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
        write_stream, write_stream_reader = anyio.create_memory_object_stream(0)
        sse_stream_writer, sse_stream_reader = anyio.create_memory_object_stream(0)

        session_id = uuid4()
        # Don't URL encode the endpoint URL, just append the session_id
        session_uri = f"{self._endpoint}?session_id={session_id.hex}"
        self._read_stream_writers[session_id] = read_stream_writer

        async def sse_writer():
            async with sse_stream_writer, write_stream_reader:
                await sse_stream_writer.send({"event": "endpoint", "data": session_uri})
                async for message in write_stream_reader:
                    await sse_stream_writer.send(
                        {
                            "event": "message",
                            "data": message.model_dump_json(
                                by_alias=True, exclude_none=True
                            ),
                        }
                    )

        async with anyio.create_task_group() as tg:
            response = EventSourceResponse(
                content=sse_stream_reader, data_sender_callable=sse_writer
            )
            tg.start_soon(response, scope, receive, send)
            # Pass read_stream and write_stream (not their counterparts) to the MCP server
            yield (read_stream, write_stream)


def create_sse_server(mcp: FastMCP):
    """Create a Starlette app that handles SSE connections and message handling"""
    transport = None

    # Define handler functions
    async def handle_sse(request):
        nonlocal transport
        if transport is None:
            # Get the base URL from the request
            server_url = f"{request.url.scheme}://{request.url.netloc}"
            # Don't URL encode the server_url or path separators
            endpoint = f"{server_url}/messages/"
            transport = CustomSseServerTransport(endpoint)

        async with transport.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await mcp._mcp_server.run(
                streams[0], streams[1], mcp._mcp_server.create_initialization_options()
            )

    # Create Starlette routes for SSE and message handling
    routes = [
        Route("/sse/", endpoint=handle_sse),
        Mount(
            "/messages/",
            app=lambda scope, receive, send: transport.handle_post_message(
                scope, receive, send
            )
            if transport
            else None,
        ),
    ]

    # Create a Starlette app
    return Starlette(routes=routes)
