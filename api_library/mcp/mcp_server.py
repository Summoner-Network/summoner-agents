from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Adder Server", json_response=True)

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b

if __name__ == "__main__":
    # Starts Streamable HTTP server at http://localhost:8000/mcp
    mcp.run(transport="streamable-http")
