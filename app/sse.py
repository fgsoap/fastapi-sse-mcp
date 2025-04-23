from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Mount, Route
# No uuid needed if the transport/mcp handles session ID generation


def create_sse_server(mcp: FastMCP):
    """Create a Starlette app that handles SSE connections and message handling"""
    # Store the configured path for reuse
    message_endpoint_path = "/messages/"
    transport = SseServerTransport(message_endpoint_path)

    # Define handler functions
    async def handle_sse(request):
        # --- Modification Start ---
        # Construct the full base URL using the request's scheme and netloc
        # Note: We construct the base URL here. The session_id seems to be
        # added by the transport/mcp logic itself within connect_sse or run.
        # We will send our own event with the full base URL first.
        full_messages_base_url = f"{request.url.scheme}://{request.url.netloc}{message_endpoint_path}"
        # --- Modification End ---

        async with transport.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            # Assuming streams[1] is the channel/stream for sending data to the client
            send_stream = streams[1]

            # --- Send the initial custom event with the FULL URL ---
            # We send this manually. Depending on how SseServerTransport works,
            # it might send its own "endpoint" event as well (potentially
            # with just the path and session_id). This manual send ensures
            # the client receives the full URL early.
            event_name = "endpoint"
            # Send the base URL. If you need the session_id appended here,
            # you'd need to know how the transport generates/provides it.
            # Sending just the base URL might be sufficient if the client knows
            # how to append the session_id it receives later.
            # Or, if the transport *only* sends the session_id later,
            # sending this full URL might be exactly what's needed.
            event_data = full_messages_base_url
            sse_message = f"event: {event_name}\ndata: {event_data}\n\n"
            await send_stream.send(sse_message.encode('utf-8'))
            # --- End of initial event sending ---

            # Now, run the main MCP server logic. It might send its own
            # events, including pings or potentially another 'endpoint' event
            # with session info.
            await mcp._mcp_server.run(
                streams[0], send_stream, mcp._mcp_server.create_initialization_options()
            )

    # Create Starlette routes for SSE and message handling
    routes = [
        Route("/sse/", endpoint=handle_sse),
        Mount(message_endpoint_path, app=transport.handle_post_message),
    ]

    # Create a Starlette app
    return Starlette(routes=routes)
