from pathlib import Path
import asyncio
from db_models import Message
from db_sdk import Database

# path to this agent's database
db_path = Path(__file__).parent / "RecvAgent_1.db"
db = Database(db_path)

async def main():
    # Fetch all distinct client addresses
    rows = await Message.find(db)
    addresses = sorted({row["address"] for row in rows})
    if not addresses:
        print("No addresses found in the database.")
        return

    # Display a numbered menu of addresses
    print("Available Addresses:")
    for idx, address in enumerate(addresses):
        print(f"  [{idx}] {address}")

    # Prompt the user to select one
    while True:
        try:
            choice = int(input("\nEnter the index of the address to view messages: "))
            if 0 <= choice < len(addresses):
                break
            else:
                print(f"Please enter a number between 0 and {len(addresses)-1}.")
        except ValueError:
            print("Invalid input. Please enter an integer index.")

    # Retrieve and display messages for the selected address
    selected_address = addresses[choice]
    msgs = await Message.find(db, where={"address": selected_address})

    print(f"\nMessages for {selected_address}:")
    if not msgs:
        print("  (no messages found)")
    else:
        for i, row in enumerate(msgs, start=1):
            print(f"  {i}. {row['content']}")

if __name__ == "__main__":
    asyncio.run(main())
    asyncio.run(db.close())
