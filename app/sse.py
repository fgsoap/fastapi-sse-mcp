from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Mount, Route
import urllib.parse
import json


class CustomSseServerTransport(SseServerTransport):
    """Custom SSE Server Transport that ensures proper URL formatting"""
    
    def __init__(self, endpoint_uri):
        # Store the raw unencoded endpoint URI
        self.raw_endpoint_uri = endpoint_uri
        super().__init__(endpoint_uri)
    
    async def _send_event(self, event, data):
        """Override the _send_event method to handle endpoint events specially"""
        if event == "endpoint":
            # For endpoint events, use the raw unencoded URI
            # Decode if it's already encoded
            if isinstance(data, str) and "%3A" in data:
                data = urllib.parse.unquote(data)
            
            # If data is not our raw endpoint (it could include query params), 
            # make sure those parts aren't encoded either
            if isinstance(data, str) and data.startswith(self.endpoint_uri):
                base = self.raw_endpoint_uri
                query = data[len(self.endpoint_uri):]
                data = base + query
                
        # Call the parent class's _send_event method with our possibly modified data
        await super()._send_event(event, data)


def create_sse_server(mcp: FastMCP):
    """Create a Starlette app that handles SSE connections and message handling"""
    
    # Define handler functions
    async def handle_sse(request):
        # Get the full URI from the request
        host = request.headers.get('host', 'localhost')
        scheme = request.headers.get('x-forwarded-proto', request.url.scheme)
        base_path = "/messages/"
        full_uri = f"{scheme}://{host}{base_path}"
        
        # Use our custom transport
        transport = CustomSseServerTransport(full_uri)
        
        async with transport.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await mcp._mcp_server.run(
                streams[0], streams[1], mcp._mcp_server.create_initialization_options()
            )

    # Function to create message handling route with full URL
    def create_message_handler():
        # For the message handler, we'll use the default localhost URL
        # The actual URL will be determined at runtime
        return CustomSseServerTransport("http://localhost/messages/").handle_post_message

    # Create Starlette routes for SSE and message handling
    routes = [
        Route("/sse/", endpoint=handle_sse),
        Mount("/messages/", app=create_message_handler()),
    ]

    # Create a Starlette app
    return Starlette(routes=routes)
