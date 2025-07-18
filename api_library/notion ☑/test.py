from notion_client import AsyncClient
import asyncio
from dotenv import load_dotenv
import os

load_dotenv()

TOKEN = os.getenv("NOTION_TOKEN")
PAGE_ID = os.getenv("NOTION_PAGE_ID")

async def agent():
    notion = AsyncClient(auth=TOKEN)

    print("🔍 Fetching page metadata...")
    page = await notion.pages.retrieve(page_id=PAGE_ID)
    print("✅ Page title:", page["properties"]["title"]["title"][0]["plain_text"])

    print("\n📦 Fetching blocks (content)...")
    children = await notion.blocks.children.list(block_id=PAGE_ID)
    
    for block in children["results"]:
        block_type = block["type"]
        block_content = block.get(block_type, {})
        
        # For paragraph/text
        if block_type == "paragraph":
            texts = block_content.get("rich_text", [])
            plain_text = "".join([t["plain_text"] for t in texts])
            print(f"📝 Paragraph: {plain_text}")
        
        elif block_type == "heading_1":
            print("🔹 Heading 1:", block_content["rich_text"][0]["plain_text"])

        elif block_type == "heading_2":
            print("🔸 Heading 2:", block_content["rich_text"][0]["plain_text"])
        
        elif block_type == "bulleted_list_item":
            print("•", block_content["rich_text"][0]["plain_text"])

        else:
            print(f"[{block_type} block]")

asyncio.run(agent())
