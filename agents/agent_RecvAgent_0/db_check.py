from pathlib import Path
import asyncio
from db_models import Message

# path to this agent’s database
db_path = Path(__file__).parent / "RecvAgent_0.db"

async def main():
    # Fetch all distinct client addresses
    rows = await Message.filter(db_path)
    addrs = sorted({row["addr"] for row in rows})
    if not addrs:
        print("No addresses found in the database.")
        return

    # Display a numbered menu of addresses
    print("Available Addresses:")
    for idx, addr in enumerate(addrs):
        print(f"  [{idx}] {addr}")

    # Prompt the user to select one
    while True:
        try:
            choice = int(input("\nEnter the index of the address to view messages: "))
            if 0 <= choice < len(addrs):
                break
            else:
                print(f"Please enter a number between 0 and {len(addrs)-1}.")
        except ValueError:
            print("Invalid input. Please enter an integer index.")

    # Retrieve and display messages for the selected address
    selected_addr = addrs[choice]
    msgs = await Message.filter(db_path, filter={"addr": selected_addr})

    print(f"\nMessages for {selected_addr}:")
    if not msgs:
        print("  (no messages found)")
    else:
        for i, row in enumerate(msgs, start=1):
            print(f"  {i}. {row['content']}")

if __name__ == "__main__":
    asyncio.run(main())
