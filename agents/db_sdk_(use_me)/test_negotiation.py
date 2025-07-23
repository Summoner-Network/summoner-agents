from pathlib import Path
import asyncio
from db_sdk_negotiation import (
    configure_db_path, init_db,
    create_or_reset_state, start_negotiation_seller, show_statistics, close_db
)

async def main():
    # point everything at the agent's own .db file
    configure_db_path(Path(__file__).parent / "Negotiator.db")
    await init_db()

    txid = await start_negotiation_seller("Negotiator")
    stats = await show_statistics("Negotiator")
    print("Started tx:", txid)
    print("Stats:", stats)
    await close_db()

if __name__ == "__main__":
    asyncio.run(main())
