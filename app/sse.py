from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Mount, Route


def create_sse_server(mcp: FastMCP):
    """Create a Starlette app that handles SSE connections and message handling"""
    # Transport will be initialized with the full URL when handling a request
    
    # Define handler functions
    async def handle_sse(request):
        # Get the full URI from the request
        host = request.headers.get('host', 'localhost')
        scheme = request.headers.get('x-forwarded-proto', request.url.scheme)
        base_path = "/messages/"
        full_uri = f"{scheme}://{host}{base_path}"
        
        # Create transport with full URI
        transport = SseServerTransport(base_path, full_uri=full_uri)
        
        async with transport.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await mcp._mcp_server.run(
                streams[0], streams[1], mcp._mcp_server.create_initialization_options()
            )

    # Create Starlette routes for SSE and message handling
    routes = [
        Route("/sse/", endpoint=handle_sse),
        Mount("/messages/", app=SseServerTransport("/messages/").handle_post_message),
    ]

    # Create a Starlette app
    return Starlette(routes=routes)
