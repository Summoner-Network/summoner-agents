# `RecvAgent_0`

This agent demonstrates how to receive messages from the Summoner server and persist them in a SQLite database using the async ORM provided by [`db_sdk.py`](db_sdk.py) (copied from the [`db_sdk_(use_me)/`](../db_sdk_(use_me)/) folder — see its README for full instructions).

## Behavior

1. On startup, the agent ensures the `messages` table (defined in [`db_models.py`](db_models.py)) exists in `RecvAgent_0.db`.  
2. Each time a message arrives with keys `"addr"` and `"content"`:  
   - Logs a receipt line via `client.logger`:  
     ```
     INFO - Received message from Client @(SocketAddress=127.0.0.1:58402)
     ```  
   - Stores the `(addr, content)` pair in the database.  
   - Queries how many messages have been stored so far for that address and logs:  
     ```
     INFO - Client @(SocketAddress=127.0.0.1:58402) has now 38 messages stored.
     ```  
3. The agent runs until you stop it (e.g. Ctrl+C).  

While it’s running, you can inspect the live data with the provided [`db_check.py`](db_check.py) script (see [**How to Run**](#how-to-run)).


## SDK Features Used

| Feature                            | Description                                                              |
|------------------------------------|--------------------------------------------------------------------------|
| `SummonerClient(name)`             | Instantiates and manages the agent                                       |
| `@client.receive(route=...)`        | Registers an async handler for incoming messages                         |
| `client.logger`                    | Logs runtime events and debugging information                            |
| `Message.create_table(db_path)`    | Ensures the `messages` table exists (async ORM from `db_sdk`)            |
| `Message.insert(db_path, ...)`     | Inserts a new row into `messages`                                        |
| `Message.filter(db_path, ...)`     | Fetches stored rows matching a filter (e.g. by `addr`)                   |
| `client.run(...)`                  | Connects to the server and starts the asyncio event loop                 |


## How to Run

1. **Start the Summoner server** (in one terminal):
    ```bash
    python server.py
    ```

2. **Run the receiver agent** (in another terminal):
    ```bash
    python agents/agent_RecvAgent_0/agent.py
    ```

3. **Inspect the database live** (in a third terminal):
    ```bash
    python agents/agent_RecvAgent_0/db_check.py
    ```
    You will see a menu like:
    ```
    Available Addresses:
        [0] 127.0.0.1:58396
        [1] 127.0.0.1:58402

    Enter the index of the address to view messages:
    ```
    Selecting an index displays the stored messages for that client address:
    ```sh
    37. {"message":"Hello Server!","from":"..."}
    38. {"message":"Hello Server!","from":"..."}
    39. {"message":"Hello Server!","from":"..."}
    ```  


## Simulation Scenarios

### Running with `SendAgent` Examples

Try running multiple senders and this receiver together:

```bash
# Terminal 1 (server)
python server.py

# Terminal 2 (sender 0 with no UUID)
python agents/agent_SendAgent_0/agent.py

# Terminal 3 (sender 1 with UUID in message)
python agents/agent_SendAgent_1/agent.py

# Terminal 4 (receiver)
python agents/agent_RecvAgent_0/agent.py

# Terminal 5 (db inspector)
python agents/agent_RecvAgent_0/db_check.py
```

You will observe both UUID-signed and plain messages being stored and counted per address.

