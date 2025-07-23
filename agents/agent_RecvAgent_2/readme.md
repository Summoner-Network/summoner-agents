# `RecvAgent_2`

This agent extends the behavior of `RecvAgent_1` with a stronger demonstration of the Summoner SDK, introducing **validation tracking**, **indexing**, and **automatic banning** of misbehaving clients.

It stores three types of data using the async ORM provided by [`db_sdk.py`](db_sdk.py) (copied from the [`db_sdk_(use_me)/`](../db_sdk_(use_me)/) folder — see its README for full instructions):

- `messages` from validated senders  
- `validations` for each attempted message  
- `banned_addresses` for persistent offenders  

## Behavior


<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. On startup, the agent initializes the database with three tables:  
   - `messages`: stores all accepted messages  
   - `validations`: logs whether each message was valid  
   - `banned_addresses`: tracks IP addresses that should be ignored  

   It also creates indexes to speed up database lookups.

2. Each time a message arrives:  
   - The `@client.hook` runs first:  
     - If the message is malformed (missing `"addr"` or `"content"`), it logs:  
       ```
       [hook:recv] missing address/content
       ```  
       and discards the message.  
     - Otherwise, it checks whether the address is already banned:  
       ```
       [hook:recv] 127.0.0.1:64790 -> Banned? True
       ```  
       If banned, the message is ignored.  
     - If not banned, it validates whether the content includes a `"from"` field (UUID).  
       - If valid:  
         - Logs:  
           ```
           [hook:recv] 127.0.0.1:64788 valid, id=3f8e3...
           ```  
         - Records a positive validation and forwards the message (with `"from"` stripped) to the receiver.  
       - If invalid:  
         - Logs:  
           ```
           [hook:recv] 127.0.0.1:64790 invalid -> checking if ban is required...
           ```  
         - Records a negative validation.  
         - If this address has sent ≥ 20 invalid messages and fewer than 50% of them were valid, it gets banned:
           ```
           [hook:recv] 127.0.0.1:64790 has been banned
           ```  

3. Validated messages are passed to the `@client.receive` handler, which:  
   - Logs the incoming message:  
     ```
     INFO - Received message from Agent @(id=3f8e3...)
     ```  
   - Stores the `(sender_id, content)` in the database  
   - Queries how many messages this sender has stored so far, and logs:  
     ```
     INFO - Agent @(id=3f8e3...) has now 22 messages stored.
     ```  

4. The agent runs until you stop it (e.g. Ctrl+C).  
   On shutdown, it closes the database connection cleanly.

While it is running, you can inspect the data with the provided [`db_check.py`](db_check.py) script (see [**How to Run**](#how-to-run)).

</details>

## SDK Features Used

| Feature                                      | Description                                                              |
|----------------------------------------------|--------------------------------------------------------------------------|
| `SummonerClient(name)`                       | Instantiates and manages the agent                                       |
| `@client.hook(direction=RECEIVE)`            | Validates or transforms messages before delivery to the receiver         |
| `@client.receive(route=...)`                 | Registers an async handler for validated messages                        |
| `client.logger`                              | Logs runtime events and debugging information                            |
| `client.loop.run_until_complete(...)`        | Executes startup coroutines (e.g., table and index creation)             |
| `Model.create_table(db)`                     | Ensures each ORM table exists                                            |
| `Model.create_index(db, name, columns, ...)` | Adds optional indexes for optimized queries                              |
| `Model.insert(...)`                          | Inserts a new row (e.g., messages, validations, bans)                    |
| `Model.insert_or_ignore(...)`                | Inserts only if the row does not already exist (e.g., banning)           |
| `Model.find(...)`                            | Fetches stored rows matching a filter                                    |
| `client.run(...)`                            | Connects to the server and starts the asyncio event loop                 |


## How to Run

1. **Start the Summoner server** (in one terminal):
    ```bash
    python server.py
    ```

2. **Run the receiver agent** (in another terminal):
    ```bash
    python agents/agent_RecvAgent_2/agent.py
    ```

3. **Inspect the database live** (in a third terminal):
    ```bash
    python agents/agent_RecvAgent_2/db_check.py
    ```
    You will see a menu like:
    ```
    Available IDs:
      [0] 3f8e3cd1-0a03-49c4-a64a-4449c36e0193

    Enter the index of the ID to view messages:
    ```
    Selecting an index displays the stored messages for that client UUID:
    ```sh
    Messages for 3f8e3cd1-0a03-49c4-a64a-4449c36e0193:
      1. {"message": "Hello Server!"}
      2. {"message": "Hello Server!"}
      ...
    ```


## Simulation Scenarios

### Running with `SendAgent` Examples

Try running both sender agents and this receiver together:

```bash
# Terminal 1 (server)
python server.py

# Terminal 2 (sender 0: no UUID → gets filtered and eventually banned)
python agents/agent_SendAgent_0/agent.py

# Terminal 3 (sender 1: includes UUID → passes validation)
python agents/agent_SendAgent_1/agent.py

# Terminal 4 (receiver with banning logic)
python agents/agent_RecvAgent_2/agent.py

# Terminal 5 (db inspector)
python agents/agent_RecvAgent_2/db_check.py
```

You will observe the following:

* Only messages from agents with a proper `"from"` field are stored
* Misbehaving clients are eventually banned after repeated invalid messages
* Once banned, their messages are ignored even if they later behave correctly

This setup demonstrates how to implement basic validation, reputation tracking, and message filtering with the Summoner SDK.
