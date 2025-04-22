from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Mount, Route
import urllib.parse
import json
import asyncio
from contextlib import asynccontextmanager


class AsyncStreamAdapter:
    """
    Adapter to make a Queue work as an async stream with context manager support.
    """
    def __init__(self, queue):
        self.queue = queue
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_value, traceback):
        pass
    
    async def receive(self):
        return await self.queue.get()
    
    async def send(self, data):
        await self.queue.put(data)


class CustomSseServerTransport(SseServerTransport):
    """Custom SSE Server Transport that ensures proper URL formatting"""
    
    def __init__(self, endpoint_uri):
        # Store the raw endpoint URI
        self.raw_endpoint_uri = endpoint_uri
        super().__init__(endpoint_uri)
    
    @asynccontextmanager
    async def connect_sse(self, scope, receive, send):
        """
        Handle SSE connection with proper URL formatting.
        Implementation using asynccontextmanager to properly support async with.
        """
        # Create SSE response headers
        headers = [(b"content-type", b"text/event-stream"),
                  (b"cache-control", b"no-cache"),
                  (b"connection", b"keep-alive")]
        
        # Send initial response
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        
        # Create reader and writer queues
        reader_queue = asyncio.Queue()
        writer_queue = asyncio.Queue()
        
        # Create stream adapters that support the async context manager protocol
        reader = AsyncStreamAdapter(reader_queue)
        writer = AsyncStreamAdapter(writer_queue)
        
        # Parse query string to get session_id
        query_string = scope.get("query_string", b"").decode("utf-8")
        session_id = ""
        if query_string and "=" in query_string:
            session_id = query_string.split("=")[1]
        
        # Send the endpoint event with properly formatted URL
        endpoint_url = f"{self.raw_endpoint_uri}?session_id={session_id}"
        event_text = f"event: endpoint\ndata: {endpoint_url}\n\n"
        await send({"type": "http.response.body", "body": event_text.encode("utf-8"), "more_body": True})
        
        # Send initial ping
        now = asyncio.get_event_loop().time()
        ping_event = f": ping - {now}\n\n"
        await send({"type": "http.response.body", "body": ping_event.encode("utf-8"), "more_body": True})
        
        # Start background task for handling the connection
        task = asyncio.create_task(self._handle_connection(scope, receive, send, reader_queue, writer_queue))
        
        try:
            # Yield control back to the caller with the streams
            yield reader, writer
        finally:
            # Clean up when the context is exited
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    
    async def _handle_connection(self, scope, receive, send, reader_queue, writer_queue):
        """Handle the ongoing SSE connection"""
        last_ping = asyncio.get_event_loop().time()
        
        try:
            while True:
                # Check for messages to send
                try:
                    message = writer_queue.get_nowait()
                    if isinstance(message, dict):
                        event_type = message.get("event", "message")
                        data = message.get("data", "")
                        
                        # Format and send the SSE event
                        event_text = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
                        await send({"type": "http.response.body", "body": event_text.encode("utf-8"), "more_body": True})
                except asyncio.QueueEmpty:
                    pass
                
                # Send ping every 15 seconds
                now = asyncio.get_event_loop().time()
                if now - last_ping > 15:
                    ping_event = f": ping - {now}\n\n"
                    await send({"type": "http.response.body", "body": ping_event.encode("utf-8"), "more_body": True})
                    last_ping = now
                
                # Check for client messages or disconnection
                try:
                    message = await asyncio.wait_for(receive(), timeout=0.1)
                    if message["type"] == "http.disconnect":
                        break
                    
                    # If there's a message, put it in the reader queue
                    await reader_queue.put(message)
                except asyncio.TimeoutError:
                    # No message received, continue
                    pass
                
                # Small delay to prevent CPU spinning
                await asyncio.sleep(0.1)
                
        except asyncio.CancelledError:
            # Task was cancelled, clean up
            raise
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
