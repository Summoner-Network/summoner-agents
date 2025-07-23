from pathlib import Path
import asyncio
from db_models import Message
from db_sdk import Database

# path to this agent's database
db_path = Path(__file__).parent / "RecvAgent_2.db"
db = Database(db_path)

async def main():
    # Fetch all distinct client IDs
    rows = await Message.find(db)
    sender_ids = sorted({row["sender_id"] for row in rows})
    if not sender_ids:
        print("No IDs found in the database.")
        return

    # Display a numbered menu of IDs
    print("Available IDs:")
    for idx, sender_id in enumerate(sender_ids):
        print(f"  [{idx}] {sender_id}")

    # Prompt the user to select one
    while True:
        try:
            choice = int(input("\nEnter the index of the ID to view messages: "))
            if 0 <= choice < len(sender_ids):
                break
            else:
                print(f"Please enter a number between 0 and {len(sender_ids)-1}.")
        except ValueError:
            print("Invalid input. Please enter an integer index.")

    # Retrieve and display messages for the selected ID
    selected_sender_id = sender_ids[choice]
    msgs = await Message.find(db, where={"sender_id": selected_sender_id})

    print(f"\nMessages for {selected_sender_id}:")
    if not msgs:
        print("  (no messages found)")
    else:
        for i, row in enumerate(msgs, start=1):
            print(f"  {i}. {row['content']}")

if __name__ == "__main__":
    asyncio.run(main())
    asyncio.run(db.close())
