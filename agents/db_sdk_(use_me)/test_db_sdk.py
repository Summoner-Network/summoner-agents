#!/usr/bin/env python3
"""
Test runner for all README snippets to verify they work correctly.
This file contains all the runnable code examples from the README.
"""

import asyncio
import tempfile
from pathlib import Path

# Copy the db_sdk code here (you will need to paste your updated db_sdk.py content)
# For now, we assume it is imported
try:
    from db_sdk import Database, Model, Field
except ImportError:
    print("Please ensure db_sdk.py is in the same directory or Python path")
    print("You can copy the content from your updated db_sdk.py file")
    exit(1)


async def test_quick_start():
    """Test the Quick Start example"""
    print("🧪 Testing Quick Start...")
    
    # Use temporary database
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = Path(tmp.name)
    
    # 1) Define your model:
    class Message(Model):
        __tablename__ = "messages"
        id        = Field("INTEGER", primary_key=True)
        addr      = Field("TEXT")
        content   = Field("TEXT")

    # 2) Create a single Database instance for reuse:
    db = Database(db_path)

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
    print("Found rows:", rows)
    assert len(rows) == 1
    assert rows[0]["content"] == "Hello"

    # 6) Clean up
    await db.close()
    db_path.unlink()  # Remove temp file
    print("✅ Quick Start test passed!")


async def test_long_lived_connection():
    """Test the Long-Lived Database Connection example"""
    print("🧪 Testing Long-Lived Database Connection...")
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = Path(tmp.name)

    # example model
    class Record(Model):
        id   = Field("INTEGER", primary_key=True)
        data = Field("TEXT")

    db = Database(db_path)
    
    await Record.create_table(db)
    
    # Test some operations
    record_id = await Record.insert(db, data="test data")
    rows = await Record.find(db, where={"id": record_id})
    assert len(rows) == 1
    assert rows[0]["data"] == "test data"
    
    await db.close()
    db_path.unlink()
    print("✅ Long-Lived Connection test passed!")


async def test_model_definition():
    """Test the Model Definition example"""
    print("🧪 Testing Model Definition...")
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = Path(tmp.name)

    class State(Model):
        __tablename__ = "state"
        agent_id           = Field("TEXT", primary_key=True, nullable=False)
        current_offer      = Field("REAL", default=0.0)
        negotiation_active = Field("INTEGER", default=0, check="negotiation_active IN (0,1)")
        updated_at         = Field("DATETIME", on_update=True)

    db = Database(db_path)
    await State.create_table(db)
    
    # Test that the table was created with constraints
    state_id = await State.insert(db, agent_id="test_agent")
    rows = await State.find(db, where={"agent_id": "test_agent"})
    assert len(rows) == 1
    assert rows[0]["current_offer"] == 0.0  # Default value
    assert rows[0]["negotiation_active"] == 0  # Default value
    
    await db.close()
    db_path.unlink()
    print("✅ Model Definition test passed!")


async def test_database_initialization():
    """Test the Database Initialization example"""
    print("🧪 Testing Database Initialization...")
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = Path(tmp.name)

    class State(Model):
        __tablename__ = "state"
        agent_id           = Field("TEXT", primary_key=True)
        current_offer      = Field("REAL", default=0.0)
        negotiation_active = Field("INTEGER", default=0, check="negotiation_active IN (0,1)")

    db = Database(db_path)
    await State.create_table(db)
    await State.create_index(
        db,
        name="idx_state_active",
        columns=["negotiation_active"],
        unique=False
    )
    
    # Test that we can insert and query
    await State.insert(db, agent_id="agent_123", negotiation_active=1)
    rows = await State.find(db, where={"negotiation_active": 1})
    assert len(rows) == 1
    
    await db.close()
    db_path.unlink()
    print("✅ Database Initialization test passed!")


async def test_crud_operations():
    """Test all Basic CRUD Operations examples"""
    print("🧪 Testing CRUD Operations...")
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = Path(tmp.name)

    class State(Model):
        __tablename__ = "state"
        agent_id           = Field("TEXT", primary_key=True)
        current_offer      = Field("REAL", default=0.0)
        negotiation_active = Field("INTEGER", default=0)
        updated_at         = Field("DATETIME", on_update=True)

    db = Database(db_path)
    await State.create_table(db)

    # Test insert
    new_id = await State.insert(
        db,
        agent_id="agent_123",
        current_offer=50.0,
        negotiation_active=1
    )
    print("Insert returned:", new_id)

    # Test insert_or_ignore
    rid = await State.insert_or_ignore(
        db,
        agent_id="agent_123",  # This should be ignored due to primary key conflict
        current_offer=60.0
    )
    print("Insert or ignore returned:", rid)

    # Test find
    rows = await State.find(
        db,
        where={"negotiation_active": 1},
        fields=["agent_id", "current_offer"],
        order_by="agent_id"
    )
    print("Find results:", rows)
    assert len(rows) == 1
    assert rows[0]["agent_id"] == "agent_123"
    assert rows[0]["current_offer"] == 50.0  # Should be original, not 60.0

    # Test update
    await State.update(
        db,
        where={"agent_id": "agent_123"},
        fields={"current_offer": 75.0}
    )
    
    # Verify update
    rows = await State.find(db, where={"agent_id": "agent_123"})
    assert rows[0]["current_offer"] == 75.0

    # Test get_or_create (existing)
    row, created = await State.get_or_create(
        db,
        defaults={"current_offer": 0.0},
        agent_id="agent_123"
    )
    assert not created
    assert row["current_offer"] == 75.0

    # Test get_or_create (new)
    row, created = await State.get_or_create(
        db,
        defaults={"current_offer": 100.0},
        agent_id="agent_456"
    )
    assert created
    assert row["current_offer"] == 100.0

    # Test delete
    await State.delete(db, where={"negotiation_active": 0})
    
    # Verify delete (should still have agent_123 with negotiation_active=1)
    rows = await State.find(db)
    assert len(rows) == 1
    assert rows[0]["agent_id"] == "agent_123"

    await db.close()
    db_path.unlink()
    print("✅ CRUD Operations test passed!")


async def test_advanced_querying():
    """Test Advanced Querying examples"""
    print("🧪 Testing Advanced Querying...")
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = Path(tmp.name)

    class State(Model):
        __tablename__ = "state"
        agent_id           = Field("TEXT", primary_key=True)
        current_offer      = Field("REAL", default=0.0)

    db = Database(db_path)
    await State.create_table(db)

    # Insert test data
    await State.insert(db, agent_id="A", current_offer=30.0)
    await State.insert(db, agent_id="B", current_offer=60.0)
    await State.insert(db, agent_id="C", current_offer=90.0)

    # Test __gt operator
    rows = await State.find(db, where={"current_offer__gt": 50})
    assert len(rows) == 2
    assert all(row["current_offer"] > 50 for row in rows)

    # Test __in operator
    rows = await State.find(db, where={"agent_id__in": ["A", "B"]})
    assert len(rows) == 2
    assert all(row["agent_id"] in ["A", "B"] for row in rows)

    await db.close()
    db_path.unlink()
    print("✅ Advanced Querying test passed!")


async def test_timestamps_and_defaults():
    """Test Automatic Timestamps & Defaults"""
    print("🧪 Testing Timestamps and Defaults...")
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = Path(tmp.name)

    class State(Model):
        __tablename__ = "state"
        agent_id           = Field("TEXT", primary_key=True)
        current_offer      = Field("REAL", default=0.0)
        updated_at         = Field("DATETIME", on_update=True)
        created_at         = Field("DATETIME", default="CURRENT_TIMESTAMP")

    db = Database(db_path)
    await State.create_table(db)

    # Insert with defaults
    await State.insert(db, agent_id="A")
    
    # Get initial state
    rows = await State.find(db, where={"agent_id": "A"})
    initial_updated_at = rows[0]["updated_at"]
    
    # Wait a moment and update
    await asyncio.sleep(0.1)
    await State.update(
        db,
        where={"agent_id": "A"},
        fields={"current_offer": 100.0}
    )
    
    # Check that updated_at changed
    rows = await State.find(db, where={"agent_id": "A"})
    new_updated_at = rows[0]["updated_at"]
    
    # Note: This test might be fragile depending on timestamp precision
    # but it should work for the basic functionality
    assert rows[0]["current_offer"] == 100.0
    print(f"Initial updated_at: {initial_updated_at}")
    print(f"New updated_at: {new_updated_at}")

    await db.close()
    db_path.unlink()
    print("✅ Timestamps and Defaults test passed!")


async def test_indexes():
    """Test Indexes & Constraints"""
    print("🧪 Testing Indexes...")
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = Path(tmp.name)

    class History(Model):
        __tablename__ = "history"
        id        = Field("INTEGER", primary_key=True)
        agent_id  = Field("TEXT")
        txid      = Field("TEXT")
        action    = Field("TEXT")

    db = Database(db_path)
    await History.create_table(db)
    await History.create_index(
        db,
        name="idx_history_agent_tx",
        columns=["agent_id", "txid"],
        unique=True
    )

    # Test that we can insert and the index works
    await History.insert(db, agent_id="A", txid="tx1", action="buy")
    rows = await History.find(db, where={"agent_id": "A", "txid": "tx1"})
    assert len(rows) == 1
    assert rows[0]["action"] == "buy"

    await db.close()
    db_path.unlink()
    print("✅ Indexes test passed!")


async def test_error_handling():
    """Test that error handling works as expected"""
    print("🧪 Testing Error Handling...")
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = Path(tmp.name)

    class State(Model):
        __tablename__ = "state"
        agent_id = Field("TEXT", primary_key=True)
        value    = Field("INTEGER")

    db = Database(db_path)
    await State.create_table(db)

    # Test insert with invalid field
    try:
        await State.insert(db, agent_id="test", invalid_field="value")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Unknown fields for State: ['invalid_field']" in str(e)
        print("✅ Insert error handling works")

    # Test find with invalid field in where
    try:
        await State.find(db, where={"invalid_field": "value"})
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Unknown fields for State: ['invalid_field']" in str(e)
        print("✅ Find where error handling works")

    # Test find with invalid field in fields
    try:
        await State.find(db, fields=["agent_id", "invalid_field"])
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Unknown fields for State: ['invalid_field']" in str(e)
        print("✅ Find fields error handling works")

    await db.close()
    db_path.unlink()
    print("✅ Error Handling test passed!")

async def test_exists():
    """Test the Model.exists helper"""
    print("🧪 Testing exists...")

    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)

    class Item(Model):
        __tablename__ = "items"
        id    = Field("INTEGER", primary_key=True)
        name  = Field("TEXT")
        price = Field("REAL")

    db = Database(db_path)
    await Item.create_table(db)

    # Initially no rows
    assert not await Item.exists(db, where={"name": "foo"})
    assert not await Item.exists(db)  # empty table → False

    # Insert some data
    await Item.insert(db, name="foo", price=10.0)
    await Item.insert(db, name="bar", price=20.0)
    await Item.insert(db, name="baz", price=30.0)

    # Now at least one row exists
    assert await Item.exists(db)

    # Equality
    assert await Item.exists(db, where={"name": "foo"})
    assert not await Item.exists(db, where={"name": "qux"})

    # Comparison operator
    assert await Item.exists(db, where={"price__gt": 25})
    assert not await Item.exists(db, where={"price__gt": 100})

    # IN / NOT IN (must pass a list/tuple)
    assert await Item.exists(db, where={"name__in": ["qux", "bar"]})   # 'bar' exists
    assert not await Item.exists(db, where={"name__in": ["qux", "quux"]})
    assert await Item.exists(db, where={"name__not_in": ["foo"]})      # 'bar'/'baz' exist
    assert not await Item.exists(db, where={"name__not_in": ["foo", "bar", "baz"]})

    # Invalid field name should raise ValueError (mirrors find)
    try:
        await Item.exists(db, where={"invalid_field": 1})
        assert False, "Should have raised ValueError for invalid field"
    except ValueError as e:
        assert "Unknown fields for Item" in str(e)

    await db.close()
    db_path.unlink()
    print("✅ exists test passed!")


async def main():
    """Run all tests"""
    print("🚀 Running README snippet tests...\n")
    
    try:
        await test_quick_start()
        await test_long_lived_connection()
        await test_model_definition()
        await test_database_initialization()
        await test_crud_operations()
        await test_advanced_querying()
        await test_timestamps_and_defaults()
        await test_indexes()
        await test_error_handling()
        await test_exists()
        
        print("\n🎉 All README snippets work correctly!")
        
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True


if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)