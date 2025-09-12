# db_sdk: A Minimal Async ORM for SQLite with AioSQLite

`db_sdk` provides a declarative layer on top of **aiosqlite**. You define your tables as Python classes using `Field` objects, and `ModelMeta` automatically generates the corresponding `CREATE TABLE` SQL. The `Database` class allows you to create a long-lived connection to your database, while the `Model` base class supplies async CRUD methods (`insert`, `insert_or_ignore`, `find`, `update`, `delete`, `get_or_create`, `exists`), flexible querying with operator suffixes, and automatic timestamp updates.

## Table of Contents

1. [Installation](#installation)  
2. [Quick Start](#quick-start)  
3. [Opening a Long-Lived Database Connection](#opening-a-long-lived-database-connection)  
4. [Defining Your Models](#defining-your-models)  
5. [Initializing the Database](#initializing-the-database)  
6. [Basic CRUD Operations](#basic-crud-operations)  
   - `insert` / `insert_or_ignore`  
   - `find`  
   - `update`  
   - `delete`  
   - `get_or_create`  
   - `exists`  
7. [Advanced Querying](#advanced-querying)  
   - Operator suffixes (`__gt`, `__lt`, `__in`, `__not_in`, etc.)  
8. [Automatic Timestamps & Defaults](#automatic-timestamps--defaults)  
9. [Indexes & Constraints](#indexes--constraints)  
10. [Putting It All Together: Example Agent](#putting-it-all-together-example-agent)  
11. [Next Steps & Extensions](#next-steps--extensions)  


## Installation

1. Ensure your project uses Python 3.8+ and install **aiosqlite**:
```bash
pip install aiosqlite
```

2. Place `db_sdk.py` in your project (e.g. next to your agent code or in a shared library folder).


## Quick Start

```python
# quick_start.py
import asyncio
from pathlib import Path
from db_sdk import Database, Model, Field

# 1) Define your model:
class Message(Model):
    __tablename__ = "messages"
    id        = Field("INTEGER", primary_key=True)
    addr      = Field("TEXT")
    content   = Field("TEXT")

async def main():
    # 2) Create a single Database instance for reuse:
    db = Database(Path("my_agent.db"))

    # 3) Initialize tables:
    await Message.create_table(db)

    # 4) Insert a record:
    msg_id = await Message.insert(
        db,
        addr="127.0.0.1:8888",
        content="Hello"
    )
    print("Inserted message id:", msg_id)

    # 5) Query records:
    rows = await Message.find(
        db,
        where={"addr": "127.0.0.1:8888"}
    )
    print(rows)  # → [{"id": 1, "addr": "...", "content": "Hello"}]

    # 6) Clean up
    await db.close()

if __name__ == "__main__":
    asyncio.run(main())
```


## Opening a Long-Lived Database Connection

Open a long-lived connection to your database by using the `Database` class:

```python
import asyncio
from pathlib import Path
from db_sdk import Database, Model, Field

# example model
class Record(Model):
    id   = Field("INTEGER", primary_key=True)
    data = Field("TEXT")

db = Database(Path("data.db"))

async def main():
    await Record.create_table(db)
    # ... do more operations ...
    await db.close()

if __name__ == "__main__":
    asyncio.run(main())
```

* **Connection pooling**: one `aiosqlite.Connection` under the hood, reused for all operations
* **`close()`**: explicitly shut down the connection when your app or script exits


## Defining Your Models

Declare tables by subclassing `Model` and using `Field`:

```python
from db_sdk import Model, Field

class State(Model):
    __tablename__ = "state"
    agent_id           = Field("TEXT", primary_key=True, nullable=False)
    current_offer      = Field("REAL", default=0.0)
    negotiation_active = Field("INTEGER", default=0, check="negotiation_active IN (0,1)")
    updated_at         = Field("DATETIME", on_update=True)
```

* `column_type`: SQLite type (`TEXT`, `INTEGER`, `REAL`, `DATETIME`).
* `primary_key`: set to `True` for primary key columns.
* `nullable`: `False` adds `NOT NULL`.
* `default`: literal value or `CURRENT_*` SQL function.
* `check`: SQL `CHECK(...)` constraint.
* `on_update`: if `True`, uses `DEFAULT CURRENT_TIMESTAMP` and auto-updates on `.update()`.


## Initializing the Database

Create tables and optional indexes before use:

```python
# init_db.py
import asyncio
from pathlib import Path
from db_sdk import Database, Model, Field

class State(Model):
    __tablename__ = "state"
    agent_id           : str   = Field("TEXT", primary_key=True)
    current_offer      : float = Field("REAL", default=0.0)
    negotiation_active : int   = Field("INTEGER", default=0, check="negotiation_active IN (0,1)")

async def main():
    db = Database(Path("negotiation.db"))
    await State.create_table(db)
    await State.create_index(
      db,
      name="idx_state_active",
      columns=["negotiation_active"],
      unique=False
    )
    await db.close()
    print("Database initialized at", db._db_path)

if __name__ == "__main__":
    asyncio.run(main())
```


## Basic CRUD Operations

> [!NOTE]
> The `insert` and `find` methods validate field names and will raise `ValueError` if you reference fields that don't exist in your model definition.

### `insert`
Creates a new record in the database with the provided field values. Returns the database-generated row ID (or `None` if no ID was generated). This method validates that all field names exist in your model definition and will raise `ValueError` for unknown fields.

```python
new_id = await State.insert(
    db,
    agent_id="agent_123",
    current_offer=50.0,
    negotiation_active=1
)
print(f"Created record with ID: {new_id}")
```

> [!TIP]
> **When to use:** When you need to create a new record and are certain it doesn't already exist, or when you want the operation to fail if there's a conflict (like a duplicate primary key).

### `insert_or_ignore`
Attempts to insert a new record, but silently ignores the operation if a conflict occurs (such as a duplicate primary key). Returns the new row ID on success, or `None` if the insert was ignored due to a conflict.

```python
row_id = await State.insert_or_ignore(
    db,
    agent_id="agent_123",  # This might already exist
    current_offer=60.0
)
if row_id:
    print(f"Created new record with ID: {row_id}")
else:
    print("Record already exists, insert was ignored")
```

> [!TIP]
> **When to use:** When you want to create a record only if it doesn't already exist, without raising an error for duplicates.

### `find`
Queries the database for records matching the conditions specified in the `where` dictionary. Returns a list of dictionaries representing the matching rows. You can optionally specify which fields to return and how to order the results. This method validates field names in both `where` conditions and `fields` lists.

```python
# Find all active negotiations
active_agents = await State.find(
    db,
    where={"negotiation_active": 1},
    fields=["agent_id", "current_offer"],
    order_by="current_offer DESC"
)

# Find a specific agent
agent = await State.find(
    db,
    where={"agent_id": "agent_123"}
)
```

> [!TIP]
> **When to use:** For querying existing data. Returns an empty list if no matches are found, so always check the length or use list indexing safely.

### `update`
Modifies existing records that match the `where` filter by updating them with the values specified in `fields`. Any fields with `on_update=True` will be automatically refreshed with the current timestamp. Does not return a value, but raises an error if the operation fails.

```python
await State.update(
    db,
    where={"agent_id": "agent_123"},
    fields={"current_offer": 75.0}
)
# If State has an 'updated_at' field with on_update=True, 
# it will automatically be set to CURRENT_TIMESTAMP
```

> [!TIP]
> **When to use:** To modify existing records. Be careful with your `where` clause to avoid accidentally updating more records than intended.

### `delete`
Removes all records that match the conditions in the `where` dictionary. This operation is permanent and cannot be undone.

```python
# Delete inactive negotiations
await State.delete(
    db, 
    where={"negotiation_active": 0}
)

# Delete a specific agent
await State.delete(
    db,
    where={"agent_id": "agent_123"}
)
```

> [!TIP]
> **When to use:** To permanently remove records. Always double-check your `where` clause, as this operation cannot be reversed. Consider using a "soft delete" pattern (marking records as deleted) for important data.

### `get_or_create`
Ensures a record exists by either finding an existing one or creating a new one if none is found. Returns a tuple of `(record_dict, created_bool)` where `created_bool` is `True` if a new record was created, `False` if an existing one was found.

```python
# Ensure an agent record exists with default values
agent_state, was_created = await State.get_or_create(
    db,
    defaults={"current_offer": 0.0, "negotiation_active": 0},
    agent_id="agent_123"
)

if was_created:
    print("Created new agent state")
else:
    print(f"Found existing agent with offer: {agent_state['current_offer']}")
```

> [!TIP]
> **When to use:** When you need to ensure a record exists before performing other operations. Common in initialization code or when handling events that might be the first interaction with a particular entity.


### `exists`

Fast existence check. Returns `True` if at least one row matches `where`; `False` otherwise. Mirrors `find`'s operator-suffix behavior and validates field names.

```python
# Check if any active negotiation exists
has_active = await State.exists(db, where={"negotiation_active": 1})

# Check presence by range and set
present = await State.exists(
    db,
    where={
        "current_offer__gt": 25,
        "agent_id__in": ["agent_1", "agent_2"]
    }
)
```

> [!TIP]
> **When to use:** short-circuit conditions, guards, and preflight checks without fetching full rows.


## Advanced Querying

You can filter records using powerful **operator suffixes** on your `where` keys. These get translated to SQL conditions behind the scenes.

### Supported Operators

| Suffix     | SQL Translation | Example value     | Description             |
| ---------- | --------------- | ----------------- | ----------------------- |
| *(none)*   | `=`             | `5`               | Equality (default)      |
| `__ne`     | `!=`            | `"inactive"`      | Not equal               |
| `__gt`     | `>`             | `10`              | Greater than            |
| `__lt`     | `<`             | `3.14`            | Less than               |
| `__gte`    | `>=`            | `100`             | Greater than or equal   |
| `__lte`    | `<=`            | `0`               | Less than or equal      |
| `__in`     | `IN (...)`      | `["A", "B", "C"]` | Must be a list or tuple |
| `__not_in` | `NOT IN (...)`  | `("X", "Y")`      | Must be a list or tuple |

> [!NOTE]
> * The default condition is equality, so `{"foo": 42}` is equivalent to `{"foo__eq": 42}`. However, do **not** use an explicit `__eq` as it is not recognized.
> * If you pass a non-list/tuple to `__in`/`__not_in`, the generated SQL will be invalid. Always pass a sequence.

### Examples

```python
# All records with offer strictly greater than 50
await State.find(db, where={"current_offer__gt": 50.0})

# All agents except one
await State.find(db, where={"agent_id__ne": "agent_123"})

# Active negotiations with price range
await State.find(
    db,
    where={
        "negotiation_active": 1,
        "current_offer__gte": 25,
        "current_offer__lt": 100
    }
)

# Filter by multiple agent IDs
await State.find(db, where={"agent_id__in": ["agent_1", "agent_2", "agent_3"]})

# Exclude banned addresses
await BannedAddress.find(db, where={"address__not_in": trusted_set})
```

### Behavior Notes

* Operator suffixes are parsed by splitting the key name at the **first `__`**.
* Unknown suffixes are treated as part of the column name (for example, `field__unknown` becomes a literal column named `field__unknown`), which typically causes a SQL error. Stick to the supported list above.
* `__in` and `__not_in` require the value to be a **list or tuple**.
* All filters are combined with **AND** logic by default (no support for OR queries).
* Queries are translated to SQL and executed directly via parameterized `aiosqlite` statements—performance depends on index usage.

### Invalid Usage

```python
# This will raise ValueError (invalid suffix)
await State.find(db, where={"current_offer__between": [20, 30]})

# This will raise TypeError (__in must use a list or tuple)
await State.find(db, where={"agent_id__in": "agent_1"})
```

### When to Use

Use advanced filtering when your query logic requires:

* Matching ranges or thresholds (`__gt`, `__lt`, etc.)
* Including/excluding subsets (`__in`, `__not_in`)
* Filtering against dynamic conditions (e.g., time windows, UUID sets)
* Banning/validating entries in a flexible rule system

```python
# Messages sent in the last hour (assuming timestamp is ISO string)
from datetime import datetime, timedelta

cutoff = (datetime.utcnow() - timedelta(hours=1)).isoformat()
await Message.find(db, where={"timestamp__gt": cutoff})
```

> [!TIP]
> You can combine filters and indexes for very efficient searches.
> If you filter frequently on a field with `__gt` or `__in`, consider creating an index.


## Automatic Timestamps & Defaults

The ORM provides several ways to handle default values and automatic timestamps, making it easy to track when records are created and modified.

### Field Defaults
You can set default values for fields that will be used when no explicit value is provided during insertion:

```python
class State(Model):
    agent_id      = Field("TEXT", primary_key=True)
    current_offer = Field("REAL", default=0.0)          # Literal default
    status        = Field("TEXT", default="pending")     # String default
    created_at    = Field("DATETIME", default="CURRENT_TIMESTAMP")  # SQL function
```

### Auto-Update Timestamps
Fields with `on_update=True` automatically get refreshed with the current timestamp whenever a record is updated using the `update()` method. This is perfect for tracking when records were last modified:

```python
class State(Model):
    agent_id   = Field("TEXT", primary_key=True)
    data       = Field("TEXT")
    updated_at = Field("DATETIME", on_update=True)  # Auto-updates on changes
```

When you call `update()`, the `updated_at` field is automatically refreshed:

```python
# Initial insert - updated_at gets DEFAULT CURRENT_TIMESTAMP
await State.insert(db, agent_id="A", data="initial")

# Later update - updated_at automatically refreshed to current time
await State.update(
    db,
    where={"agent_id": "A"},
    fields={"data": "modified"}
)
# The 'updated_at' column is automatically set to the current timestamp
```

### Combining Creation and Update Timestamps
A common pattern is to track both when records are created and when they're last modified:

```python
class AuditedModel(Model):
    id         = Field("INTEGER", primary_key=True)
    data       = Field("TEXT")
    created_at = Field("DATETIME", default="CURRENT_TIMESTAMP")
    updated_at = Field("DATETIME", on_update=True)
```

**Note:** Auto-update only happens during `update()` operations. Direct SQL modifications or `insert()` operations won't trigger the auto-update behavior.

## Indexes & Constraints

Indexes improve query performance and can enforce uniqueness constraints. Create them after your tables are set up to speed up common queries and prevent duplicate data.

### Creating Basic Indexes
Indexes speed up queries on frequently searched columns:

```python
# Single column index for faster lookups
await State.create_index(
    db,
    name="idx_state_agent",
    columns=["agent_id"],
    unique=False
)

# Multi-column index for compound queries
await History.create_index(
    db,
    name="idx_history_agent_time",
    columns=["agent_id", "timestamp"],
    unique=False
)
```

### Unique Indexes
Unique indexes prevent duplicate combinations of values, acting as additional constraints:

```python
# Ensure each agent can only have one active transaction
await State.create_index(
    db,
    name="idx_unique_active_tx",
    columns=["agent_id", "transaction_id"],
    unique=True
)
```

### Field-Level Constraints
You can also add constraints directly in field definitions:

```python
class State(Model):
    agent_id           = Field("TEXT", primary_key=True)
    negotiation_active = Field("INTEGER", default=0, check="negotiation_active IN (0,1)")
    current_offer      = Field("REAL", check="current_offer >= 0")
```

### When to Create Indexes

**Create indexes for:**
- Columns frequently used in `WHERE` clauses
- Columns used in `ORDER BY` operations
- Foreign key relationships (if you implement them)
- Combinations of columns often queried together

**Avoid over-indexing:**
- Indexes speed up reads but slow down writes
- Each index requires storage space
- Too many indexes can hurt overall performance

```python
# Good: Index on frequently queried field
await Message.create_index(db, "idx_msg_timestamp", ["timestamp"])

# Good: Compound index for common query pattern
await Transaction.create_index(db, "idx_tx_agent_status", ["agent_id", "status"])

# Avoid: Indexing rarely queried fields
# await Data.create_index(db, "idx_rarely_used", ["rarely_queried_field"])
```


## Putting It All Together: Example Agent

```python
# agents/agent_Negotiator/agent.py
import asyncio, argparse, json
from pathlib import Path
from summoner.client import SummonerClient
from db_sdk import Database, Model, Field

# --- Model definition ---
class State(Model):
    __tablename__ = "state"
    agent_id           : str   = Field("TEXT", primary_key=True)
    transaction_id     : str   = Field("TEXT", default=None)
    current_offer      : float = Field("REAL", default=0.0)
    negotiation_active : int   = Field("INTEGER", default=0, check="negotiation_active IN (0,1)")

db = Database(Path(__file__).parent / "Negotiator.db")

async def setup_db():
    # ensure table and index exist
    await State.create_table(db)
    await State.create_index(
        db,
        name="idx_agent_tx",
        columns=["agent_id","transaction_id"],
        unique=True
    )

client = SummonerClient(name="Negotiator")

@client.receive(route="offer")
async def on_offer(msg):
    # ensure a row exists
    row, created = await State.get_or_create(db, agent_id=client.name)
    # update state based on incoming offer
    await State.update(
        db,
        where={"agent_id": client.name},
        fields={
            "current_offer": msg["price"],
            "negotiation_active": 1,
            "transaction_id": msg.get("txid")
        }
    )

    # Fast guard (exists)
    if await State.exists(db, where={"negotiation_active": 1}):
        # do something knowing at least one negotiation is active
        ...

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", help="path to config JSON")
    args = parser.parse_args()

    client.loop.run_until_complete(setup_db())

    try:
        client.run(host="127.0.0.1", port=8888, config_path=args.config or "configs/client_config.json")
    finally:
        # cleanly close the connection on shutdown
        asyncio.run(db.close())
```

---

With `db_sdk`, you keep agent logic focused on behavior, while data models and access remain concise and declarative. Enjoy building!
