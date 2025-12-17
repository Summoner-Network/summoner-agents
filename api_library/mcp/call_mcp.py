import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def call_tool(mcp_url: str, tool_name: str, arguments: dict):
    async with streamablehttp_client(mcp_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool_name, arguments=arguments)

def call_tool_sync(mcp_url: str, tool_name: str, arguments: dict):
    return asyncio.run(call_tool(mcp_url, tool_name, arguments))

if __name__ == "__main__":
    out = call_tool_sync("http://localhost:8000/mcp", "add", {"a": 5, "b": 3})
    print(out)
