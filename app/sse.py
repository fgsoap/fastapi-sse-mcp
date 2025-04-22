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
        
        # Create transport with full_uri parameter
        transport = SseServerTransport(full_uri)
        
        async with transport.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await mcp._mcp_server.run(
                streams[0], streams[1], mcp._mcp_server.create_initialization_options()
            )

    # Function to create message handling route with full URL
    def create_message_handler(request=None):
        if request:
            host = request.headers.get('host', 'localhost')
            scheme = request.headers.get('x-forwarded-proto', request.url.scheme)
        else:
            # Default values when not in a request context
            host = 'localhost'
            scheme = 'http'
            
        base_path = "/messages/"
        full_uri = f"{scheme}://{host}{base_path}"
        return SseServerTransport(full_uri).handle_post_message

    # Create Starlette routes for SSE and message handling
    routes = [
        Route("/sse/", endpoint=handle_sse),
        # We'll dynamically set the full URL when handling the message request
        Mount("/messages/", app=create_message_handler()),
    ]

    # Create a Starlette app
    return Starlette(routes=routes)
