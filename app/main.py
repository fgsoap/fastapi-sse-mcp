from fastapi import FastAPI
from app.sse import create_sse_server
from mcp.server.fastmcp import FastMCP

app = FastAPI()
mcp = FastMCP("MCP-SSE")

# Mount the Starlette SSE server onto the FastAPI app
app.mount("/", create_sse_server(mcp))


@app.get("/")
def read_root():
    return {"Hello": "World"}

@mcp.tool(name="get_dfm_sap", description="Get the SAP in DFM for a given technology")
async def get_dfm_sap(technology: str) -> str:
    return f"Querying SAP for {technology}"

# @mcp.tool(name="get_dfm_sap", description="Get the SAP for a given technology")
# async def get_dfm_sap(technology: str) -> str:
#     """Get the SAP for a given technology using c3h dfm sap command"""
#     import subprocess
#     import os.path
#     import json

#     c3h_path = r"C:\Users\jianxu1\.cargo\bin\c3h.exe"
#     settings_path = (
#         r"C:\Users\jianxu1\Documents\Python_Projects\MCP-Start\settings.toml"
#     )

#     # Check if c3h exists
#     if not os.path.exists(c3h_path):
#         return f"Error: c3h executable not found at {c3h_path}"

#     try:
#         # First try with detailed output format
#         command = [c3h_path, settings_path, "dfm", "sap", "-t", technology]
#         result = subprocess.run(command, capture_output=True, text=True, check=False)
#         return f"SAP for {technology}:\n{result.stdout}"
#     except Exception as e:
#         return f"Error running c3h dfm sap command: {str(e)}"

@mcp.resource("echo://{message}")
def echo_resource(message: str) -> str:
    """Echo a message as a resource"""
    return f"Resource echo: {message}"
