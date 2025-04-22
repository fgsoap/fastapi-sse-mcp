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
    Also implements the async iterator protocol for use with async for loops.
    """
    def __init__(self, queue):
        self.queue = queue
        self._closed = False
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_value, traceback):
        self._closed = True
    
    def __aiter__(self):
        return self
    
    async def __anext__(self):
        if self._closed:
            raise StopAsyncIteration
        
        try:
            return await self.queue.get()
        except Exception:
            if self._closed:
                raise StopAsyncIteration
            raise
    
    async def receive(self):
        message = await self.queue.get()
        
        # Process the message to make it compatible with MCP
        if isinstance(message, dict):
            # Validate the message structure before processing
            try:
                # Create a dynamic object with a root attribute containing the message
                # This is what MCP expects - an object with a root attribute
                message_obj = type('MCPMessage', (), {'root': message})
                return message_obj
            except Exception as e:
                print(f"Error processing message: {e}")
                # Return a valid object that indicates an error
                error_message = {"error": str(e)}
                return type('MCPErrorMessage', (), {'root': error_message})
        
        # Otherwise, return the message as is
        return message
    
    async def send(self, data):
        await self.queue.put(data)
        
    def close(self):
        self._closed = True


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
        if query_string:
            # Parse the query string more robustly
            query_params = urllib.parse.parse_qs(query_string)
            # Get the first session_id value if present
            session_id = query_params.get("session_id", [""])[0]
        
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
                        try:
                            # Ensure data is properly JSON serializable
                            json_data = json.dumps(data)
                            event_text = f"event: {event_type}\ndata: {json_data}\n\n"
                            await send({"type": "http.response.body", "body": event_text.encode("utf-8"), "more_body": True})
                        except (TypeError, ValueError) as e:
                            print(f"Error serializing message data: {e}")
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
        
        # Create our custom transport
        transport = CustomSseServerTransport(full_uri)
        
        try:
            async with transport.connect_sse(
                request.scope, request.receive, request._send
            ) as streams:
                await mcp._mcp_server.run(
                    streams[0], streams[1], mcp._mcp_server.create_initialization_options()
                )
        except Exception as e:
            print(f"Error in SSE handler: {str(e)}")
            # Return a response if the connection is still open
            try:
                # Send error event
                error_event = f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                await request._send({"type": "http.response.body", "body": error_event.encode("utf-8"), "more_body": False})
            except Exception:
                # Connection may already be closed, ignore
                pass
    
    # Get the full base URI for the messages endpoint
    host = "localhost"  # Default fallback
    scheme = "http"     # Default fallback
    # Note: We're setting defaults here since we're not in a request context
    # The actual values will be determined at request time in handle_sse
    
    base_path = "/messages/"
    full_uri = f"{scheme}://{host}{base_path}"
    
    # Create a single transport instance to be used for both routes
    message_transport = CustomSseServerTransport(full_uri)

    # Create Starlette routes for SSE and message handling
    routes = [
        Route("/sse/", endpoint=handle_sse),
        Mount("/messages/", app=message_transport.handle_post_message),
    ]

    # Create a Starlette app
    return Starlette(routes=routes)
