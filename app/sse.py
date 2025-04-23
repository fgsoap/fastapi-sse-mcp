from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Mount, Route
# No uuid import needed if not generating unique IDs

def create_sse_server(mcp: FastMCP):
    """Create a Starlette app that handles SSE connections and message handling"""
    message_endpoint_path = "/messages/"
    transport = SseServerTransport(message_endpoint_path)

    # Define handler functions
    async def handle_sse(request):
        # Construct the base URL for the messages endpoint
        # It uses the scheme and host/port from the incoming request
        # No unique session_id is added here
        messages_url = f"{request.url.scheme}://{request.url.netloc}{message_endpoint_path}"

        # If you literally want "?session_id=" at the end:
        # messages_url = f"{request.url.scheme}://{request.url.netloc}{message_endpoint_path}?session_id="

        async with transport.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            # Assuming streams[1] is the channel/stream for sending data to the client
            send_stream = streams[1]

            # --- Send the initial custom event ---
            event_name = "endpoint"
            event_data = messages_url
            sse_message = f"event: {event_name}\ndata: {event_data}\n\n"
            await send_stream.send(sse_message.encode('utf-8'))
            # --- End of initial event sending ---

            # Now, run the main MCP server logic
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
