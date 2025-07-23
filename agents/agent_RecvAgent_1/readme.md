# `RecvAgent_1`

This agent builds on `RecvAgent_0` by introducing a **validation hook** using the Summoner SDK's `@client.hook` feature. It receives messages from the Summoner server and stores them in a local SQLite database via the async ORM in [`db_sdk.py`](db_sdk.py) (copied from the [`db_sdk_(use_me)/`](../db_sdk_(use_me)/) folder — see its README for full instructions).

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. On startup, the agent ensures the `messages` table (defined in [`db_models.py`](db_models.py)) exists in `RecvAgent_1.db`.  
2. When a message is received:  
   - A **hook** (`@client.hook`) checks that the message is a dictionary with `"addr"` and `"content"`.  
     - If validation fails, the hook logs:
       ```
       [hook:recv] missing address/content
       ```
       and the message is **not processed further**.
     - If validation passes, the hook logs:
       ```
       [hook:recv] 127.0.0.1:64790 passed validation
       ```
   - The validated message is then passed to the `@client.receive` handler, which:  
     - Logs a receipt line:  
       ```
       INFO - Received message from Client @(SocketAddress=127.0.0.1:64790).
       ```  
     - Stores the `(addr, content)` pair in the database.  
     - Queries how many messages have been stored for that address and logs:
       ```
       INFO - Client @(SocketAddress=127.0.0.1:64790) has now 2 messages stored.
       ```  
3. The agent runs until you stop it (e.g. Ctrl+C).  

While it is running, you can inspect the live data with the provided [`db_check.py`](db_check.py) script (see [**How to Run**](#how-to-run)).

</details>

## SDK Features Used

| Feature                                | Description                                                              |
|----------------------------------------|--------------------------------------------------------------------------|
| `SummonerClient(name)`                 | Instantiates and manages the agent                                       |
| `@client.hook(direction=RECEIVE)`      | Validates or transforms messages *before* they reach the receive handler |
| `@client.receive(route=...)`           | Registers an async handler for validated messages                        |
| `client.logger`                        | Logs runtime events and debugging information                            |
| `client.loop.run_until_complete(...)` | Executes a coroutine on the client's internal asyncio loop (e.g., table creation) |
| `Message.create_table(db)`             | Ensures the `messages` table exists (async ORM from `db_sdk`)            |
| `Message.insert(db, ...)`              | Inserts a new row into `messages`                                        |
| `Message.find(db, ...)`                | Fetches stored rows matching a filter (e.g. by `address`)                |
| `client.run(...)`                      | Connects to the server and starts the asyncio event loop                 |

## How to Run

1. **Start the Summoner server** (in one terminal):
    ```bash
    python server.py
    ```

2. **Run the receiver agent** (in another terminal):
    ```bash
    python agents/agent_RecvAgent_1/agent.py
    ```

3. **Inspect the database live** (in a third terminal):
    ```bash
    python agents/agent_RecvAgent_1/db_check.py
    ```
    You will see a menu like:
    ```
    Available Addresses:
        [0] 127.0.0.1:64790
        [1] 127.0.0.1:64788

    Enter the index of the address to view messages:
    ```
    Selecting an index displays the stored messages for that client address:
    ```sh
    1. {"message":"Hello Server!","from":"..."}
    2. {"message":"Hello Server!","from":"..."}
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

# Terminal 4 (receiver with hook)
python agents/agent_RecvAgent_1/agent.py

# Terminal 5 (db inspector)
python agents/agent_RecvAgent_1/db_check.py
```

You will observe the hook validation step in action, with only well-formed messages being passed along to the receive handler and stored in the database.

