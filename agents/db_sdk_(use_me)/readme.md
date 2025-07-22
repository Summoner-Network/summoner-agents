## db_sdk: A Minimal Async ORM for SQLite with AioSQLite

`db_sdk` provides a declarative on top of **aiosqlite**. You define your tables as Python classes using `Field` objects, and `ModelMeta` automatically generates the corresponding `CREATE TABLE` SQL. The `Model` base class then supplies async CRUD methods (`insert`, `insert_or_ignore`, `filter`, `update`, `delete`, etc.), flexible querying with operator suffixes, and automatic timestamp updates.

### Table of Contents

1. [Installation](#installation)
2. [Quick Start](#quick-start)
3. [Defining Your Models](#defining-your-models)
4. [Initializing the Database](#initializing-the-database)
5. [Basic CRUD Operations](#basic-crud-operations)
   - `insert` / `insert_or_ignore`
   - `filter`
   - `update`
   - `delete`
6. [Advanced Querying](#advanced-querying)
   - Operator suffixes (`__gt`, `__lt`, `__in`, etc.)
7. [Automatic Timestamps](#automatic-timestamps)
8. [Indexes & Constraints](#indexes--constraints)
9. [Putting It All Together: Example Agent](#putting-it-all-together-example-agent)
10. [Next Steps & Extensions](#next-steps--extensions)


## Installation

1. Ensure your project uses Python 3.8+ and install **aiosqlite**:
   ```bash
   pip install aiosqlite
   ```
2. Place `db_sdk.py` in your project (adjacent to agent code or in a shared library folder).


## Quick Start

```python
# quick_start.py
import asyncio
from pathlib import Path
from db_sdk import Model, Field

# 1) Define your model:
class Message(Model):
    __tablename__ = "messages"
    id      : int    = Field("INTEGER", primary_key=True)
    addr    : str    = Field("TEXT")
    content : str    = Field("TEXT")

async def main():
    # 2) Initialize the database file and create tables:
    db_path = Path("my_agent.db")
    await Message.create_table(db_path)

    # 3) Insert a record:
    msg_id = await Message.insert(
        db_path,
        addr="127.0.0.1:8888",
        content="Hello"
    )
    print("Inserted message id:", msg_id)

    # 4) Query records:
    rows = await Message.filter(
        db_path,
        filter={"addr": "127.0.0.1:8888"}
    )
    print(rows)  # → [{"id": 1, "addr": "...", "content": "Hello"}]

if __name__ == "__main__":
    asyncio.run(main())
```


## Defining Your Models

Declare tables by subclassing `Model` and using `Field`:

```python
from db_sdk import Model, Field

class State(Model):
    __tablename__ = "state"
    agent_id           : str   = Field("TEXT", primary_key=True)
    current_offer      : float = Field("REAL", default=0.0)
    negotiation_active : int   = Field("INTEGER", default=0, check="negotiation_active IN (0,1)")
    updated_at         : str   = Field("DATETIME", default="CURRENT_TIMESTAMP", on_update=True)
```

- `column_type`: SQLite type (`TEXT`, `INTEGER`, `REAL`, `DATETIME`).
- `primary_key`: set to `True` for primary key columns.
- `default`: literal default value (strings auto-quoted).
- `check`: SQL `CHECK(...)` constraint.
- `on_update`: if `True`, column resets to `CURRENT_TIMESTAMP` on `update()`.


## Initializing the Database

Create tables and optional indexes before use:

```python
# init_db.py
import asyncio
from pathlib import Path
from db_sdk import Model, Field

class State(Model):
    __tablename__ = "state"
    agent_id           : str   = Field("TEXT", primary_key=True)
    current_offer      : float = Field("REAL", default=0.0)
    negotiation_active : int   = Field("INTEGER", default=0, check="negotiation_active IN (0,1)")

async def main():
    db_path = Path("negotiation.db")
    # Create table (and any indexes)
    await State.create_table(db_path)
    await State.create_index(
        db_path,
        name="idx_state_active",
        columns=["negotiation_active"],
        unique=False
    )
    print("Database initialized at", db_path)

if __name__ == "__main__":
    asyncio.run(main())

```


## Basic CRUD Operations

### `insert`
Insert a row; returns the new row’s ID:

```python
new_id = await State.insert(
    db_path,
    agent_id="agent_123",
    current_offer=50.0,
    negotiation_active=1
)
```

### `insert_or_ignore`
Insert unless a conflict occurs (returns `None` if skipped):

```python
rid = await State.insert_or_ignore(
    db_path,
    agent_id="agent_123",
    current_offer=60.0
)
```

### `filter`
Query rows by equality:

```python
rows = await State.filter(
    db_path,
    filter={"negotiation_active": 1},
    fields=["agent_id","current_offer"],
    order_by="agent_id"
)
```

### `update`
Update rows matching `where`:

```python
await State.update(
    db_path,
    where={"agent_id":"agent_123"},
    fields={"current_offer":75.0}
)
```

### `delete`
Delete rows matching a filter:

```python
await State.delete(db_path, filter={"negotiation_active":0})
```


## Advanced Querying

Leverage operator suffixes in `filter`:

- `__gt`, `__lt`, `__gte`, `__lte`, `__ne`
- `__in` for lists or tuples

```python
# Offers greater than 50
await State.filter(db_path, filter={"current_offer__gt":50})
# Filter by multiple agents
await State.filter(db_path, filter={"agent_id__in":["A","B"]})
```


## Automatic Timestamps

Fields with `on_update=True` get `CURRENT_TIMESTAMP` on updates:

```python
await State.update(
    db_path,
    where={"agent_id":"A"},
    fields={"current_offer":100.0}
)
# 'updated_at' column automatically refreshed
```


## Indexes & Constraints

Create unique or non-unique indexes:

```python
await History.create_index(
    db_path,
    name="idx_history_agent_tx",
    columns=["agent_id","txid"],
    unique=True
)
```


## Putting It All Together: Example Agent

```python
# agents/agent_Negotiator/agent.py
import argparse
import asyncio
from pathlib import Path
from summoner.client import SummonerClient
from db_sdk import Model, Field

# --- Model definition ---
class State(Model):
    __tablename__ = "state"
    agent_id           : str   = Field("TEXT", primary_key=True)
    transaction_id     : str   = Field("TEXT", default=None)
    current_offer      : float = Field("REAL", default=0.0)
    negotiation_active : int   = Field("INTEGER", default=0, check="negotiation_active IN (0,1)")

# --- Agent setup ---
db_path = Path(__file__).parent / "Negotiator.db"

async def setup_db():
    # ensure table and index exist
    await State.create_table(db_path)
    await State.create_index(
        db_path,
        name="idx_agent_tx",
        columns=["agent_id","transaction_id"],
        unique=True
    )

client = SummonerClient(name="Negotiator")

@client.receive(route="offer")
async def on_offer(msg):
    # ensure a row exists
    row, created = await State.get_or_create(db_path, agent_id=client.name)
    # update state based on incoming offer
    await State.update(
        db_path,
        where={"agent_id": client.name},
        fields={
            "current_offer": msg["price"],
            "negotiation_active": 1,
            "transaction_id": msg.get("txid")
        }
    )

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", help="path to config JSON")
    args = parser.parse_args()

    async def main():
        await setup_db()
        client.run(
            host="127.0.0.1",
            port=8888,
            config_path=args.config or "configs/default.json"
        )

    asyncio.run(main())
```

---

With `db_sdk`, you keep agent logic focused on behavior, while data models and access remain concise and declarative. Enjoy building!
