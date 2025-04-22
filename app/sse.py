from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Mount, Route
import urllib.parse
import json
import asyncio


class CustomSseServerTransport(SseServerTransport):
    """Custom SSE Server Transport that ensures proper URL formatting"""
    
    def __init__(self, endpoint_uri):
        # Store both the raw and URL-encoded versions
        self.raw_endpoint_uri = endpoint_uri
        # We'll use a dummy value for the parent class and override its usage
        super().__init__("dummy")
        # Then override the endpoint_uri property
        self._endpoint_uri = endpoint_uri
    
    async def connect_sse(self, scope, receive, send):
        """Override connect_sse to inject our custom logic"""
        # Create SSE response headers
        headers = [(b"content-type", b"text/event-stream"),
                  (b"cache-control", b"no-cache"),
                  (b"connection", b"keep-alive"),
                  (b"transfer-encoding", b"chunked")]
        
        # Send initial response
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        
        # Create reader and writer queues
        reader = asyncio.Queue()
        writer = asyncio.Queue()
        
        # Send the endpoint event with the properly formatted URL
        # We'll directly craft the SSE event to avoid any encoding
        session_id = scope["query_string"].decode("utf-8").split("=")[1] if b"=" in scope["query_string"] else ""
        endpoint_url = f"{self.raw_endpoint_uri}?session_id={session_id}"
        
        # Format the SSE event text manually to avoid encoding
        event_text = f"event: endpoint\ndata: {endpoint_url}\n\n"
        await send({"type": "http.response.body", "body": event_text.encode("utf-8"), "more_body": True})
        
        # Start the transport background task
        background_task = asyncio.create_task(self._handle_connection(scope, receive, send, reader, writer))
        
        try:
            # Return the reader and writer for MCP to use
            yield reader, writer
        finally:
            # Clean up the background task when done
            background_task.cancel()
    
    async def _handle_connection(self, scope, receive, send, reader, writer):
        """Handle the ongoing SSE connection after initial setup"""
        try:
            # Process incoming messages and send SSE events
            last_ping = asyncio.get_event_loop().time()
            
            while True:
                # Send ping every 15 seconds
                now = asyncio.get_event_loop().time()
                if now - last_ping > 15:
                    ping_event = f": ping - {now}\n\n"
                    await send({"type": "http.response.body", "body": ping_event.encode("utf-8"), "more_body": True})
                    last_ping = now
                
                # Process any messages from the writer queue
                try:
                    message = writer.get_nowait()
                    if isinstance(message, dict):
                        event_type = message.get("event", "message")
                        data = message.get("data", "")
                        
                        # Format and send the SSE event
                        event_text = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
                        await send({"type": "http.response.body", "body": event_text.encode("utf-8"), "more_body": True})
                except asyncio.QueueEmpty:
                    pass
                
                # Check for client disconnect
                message = await asyncio.wait_for(receive(), timeout=1.0)
                if message["type"] == "http.disconnect":
                    break
                
                # Small delay to prevent CPU spinning
                await asyncio.sleep(0.1)
                
        except asyncio.CancelledError:
            # Task was cancelled, clean up
            pass
        except Exception as e:
            # Log any errors
            print(f"Error in SSE connection: {e}")


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

    # Create Starlette routes for SSE and message handling
    routes = [
        Route("/sse/", endpoint=handle_sse),
        Mount("/messages/", app=SseServerTransport("/messages/").handle_post_message),
    ]

    # Create a Starlette app
    return Starlette(routes=routes)
