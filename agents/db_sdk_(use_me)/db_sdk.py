import aiosqlite
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


# --- Database Helper --------------------------------
class Database:
    """
    Simple wrapper to manage a single aiosqlite connection per database file.
    """
    def __init__(self, db_path: Union[Path, str]):
        self._db_path = Path(db_path)
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> aiosqlite.Connection:
        if self._conn is None:
            self._conn = await aiosqlite.connect(str(self._db_path))
            self._conn.row_factory = aiosqlite.Row
        return self._conn

    async def execute(self, sql: str, params: Tuple[Any, ...] = ()) -> aiosqlite.Cursor:
        db = await self.connect()
        return await db.execute(sql, params)

    async def executemany(self, sql: str, params_list: List[Tuple[Any, ...]]) -> aiosqlite.Cursor:
        db = await self.connect()
        return await db.executemany(sql, params_list)

    async def fetchall(self, sql: str, params: Tuple[Any, ...] = ()) -> List[aiosqlite.Row]:
        cur = await self.execute(sql, params)
        return await cur.fetchall()

    async def fetchone(self, sql: str, params: Tuple[Any, ...] = ()) -> Optional[aiosqlite.Row]:
        cur = await self.execute(sql, params)
        return await cur.fetchone()

    async def commit(self) -> None:
        db = await self.connect()
        await db.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None


# --- Field Definition --------------------------------
class Field:
    def __init__(
        self,
        column_type: str,
        primary_key: bool = False,
        default: Any = None,
        check: Optional[str] = None,
        on_update: bool = False,
        nullable: bool = True
    ):
        self.column_type = column_type
        self.primary_key = primary_key
        self.default = default
        self.check = check
        self.on_update = on_update
        self.nullable = nullable


# --- Operator mapping --------------------------------
_OPERATOR_MAP = {
    'gt': '>',
    'lt': '<',
    'gte': '>=',
    'lte': '<=',
    'ne': '!=',
    'in': 'IN',
    'not_in': 'NOT IN'
}


# --- Model Metaclass --------------------------------
class ModelMeta(type):
    def __init__(cls, name, bases, attrs):
        if name == 'Model':
            return

        # Table name (use __tablename__ if provided, else class name lowercased)
        cls.__tablename__ = getattr(cls, '__tablename__', cls.__name__.lower())

        # Collect Field instances from class attrs
        cls._fields: Dict[str, Field] = {
            key: val for key, val in attrs.items() if isinstance(val, Field)
        }

        # Build CREATE TABLE SQL
        cols: List[str] = []
        for fname, fld in cls._fields.items():
            col_def = f"{fname} {fld.column_type}"
            if not fld.nullable:
                col_def += ' NOT NULL'
            if fld.primary_key:
                col_def += ' PRIMARY KEY'
            if fld.default is not None:
                if isinstance(fld.default, str) and not fld.default.upper().startswith('CURRENT_'):
                    default_val = f"'{fld.default}'"
                else:
                    default_val = str(fld.default)
                col_def += f' DEFAULT {default_val}'
            elif fld.on_update:
                col_def += ' DEFAULT CURRENT_TIMESTAMP'
            if fld.check:
                col_def += f' CHECK({fld.check})'
            cols.append(col_def)

        cls._create_sql = (
            f"CREATE TABLE IF NOT EXISTS {cls.__tablename__} (" +
            ", ".join(cols) + ")"
        )

    async def create_table(cls, db: Union[Database, Path, str]) -> None:
        db_conn = db if isinstance(db, Database) else Database(db)
        await db_conn.execute(cls._create_sql)
        await db_conn.commit()

    async def create_index(
        cls,
        db: Union[Database, Path, str],
        name: str,
        columns: List[str],
        unique: bool = False
    ) -> None:
        db_conn = db if isinstance(db, Database) else Database(db)
        cols = ", ".join(columns)
        uq = 'UNIQUE ' if unique else ''
        sql = f"CREATE {uq}INDEX IF NOT EXISTS {name} ON {cls.__tablename__}({cols})"
        await db_conn.execute(sql)
        await db_conn.commit()


# --- Base Model --------------------------------------
class Model(metaclass=ModelMeta):
    @classmethod
    async def insert(
        cls,
        db: Union[Database, Path, str],
        **kwargs: Any
    ) -> Optional[int]:
        db_conn = db if isinstance(db, Database) else Database(db)
        unknown_fields = [k for k in kwargs.keys() if k not in cls._fields]
        if unknown_fields:
            raise ValueError(f"Unknown fields for {cls.__name__}: {unknown_fields}")
        keys, vals = zip(*[(k, v) for k, v in kwargs.items() if k in cls._fields])
        cols = ", ".join(keys)
        ph = ", ".join("?" for _ in keys)
        sql = f"INSERT INTO {cls.__tablename__}({cols}) VALUES ({ph})"
        cur = await db_conn.execute(sql, vals)
        await db_conn.commit()
        return cur.lastrowid

    @classmethod
    async def insert_or_ignore(
        cls,
        db: Union[Database, Path, str],
        **kwargs: Any
    ) -> Optional[int]:
        db_conn = db if isinstance(db, Database) else Database(db)
        keys, vals = zip(*[(k, v) for k, v in kwargs.items() if k in cls._fields])
        cols = ", ".join(keys)
        ph = ", ".join("?" for _ in keys)
        sql = (
            f"INSERT OR IGNORE INTO {cls.__tablename__}({cols}) "
            f"VALUES ({ph})"
        )
        cur = await db_conn.execute(sql, vals)
        await db_conn.commit()
        return cur.lastrowid or None

    @classmethod
    async def find(
        cls,
        db: Union[Database, Path, str],
        where: Dict[str, Any] = None,
        fields: List[str] = None,
        order_by: str = None
    ) -> List[Dict[str, Any]]:
        db_conn = db if isinstance(db, Database) else Database(db)
        if fields:
            invalid_fields = [f for f in fields if f not in cls._fields]
            if invalid_fields:
                raise ValueError(f"Unknown fields for {cls.__name__}: {invalid_fields}")
        cols = fields or list(cls._fields.keys())
        col_sql = ", ".join(cols)
        sql = f"SELECT {col_sql} FROM {cls.__tablename__}"
        params: List[Any] = []
        if where:
            invalid_fields = [k.split('__')[0] for k in where.keys() if k.split('__')[0] not in cls._fields]
            if invalid_fields:
                raise ValueError(f"Unknown fields for {cls.__name__}: {invalid_fields}")
            conditions = []
            for key, val in where.items():
                if '__' in key:
                    fname, op = key.split('__', 1)
                    sql_op = _OPERATOR_MAP.get(op)
                    if sql_op == 'IN' and isinstance(val, (list, tuple)):
                        placeholders = ",".join("?" for _ in val)
                        conditions.append(f"{fname} IN ({placeholders})")
                        params.extend(val)
                    elif sql_op == 'NOT IN' and isinstance(val, (list, tuple)):
                        placeholders = ",".join("?" for _ in val)
                        conditions.append(f"{fname} NOT IN ({placeholders})")
                        params.extend(val)
                    elif sql_op:
                        conditions.append(f"{fname} {sql_op} ?")
                        params.append(val)
                    else:
                        conditions.append(f"{key} = ?")
                        params.append(val)
                else:
                    conditions.append(f"{key} = ?")
                    params.append(val)
            sql += " WHERE " + " AND ".join(conditions)
        if order_by:
            sql += f" ORDER BY {order_by}"

        rows = await db_conn.fetchall(sql, tuple(params))
        return [dict(row) for row in rows]

    @classmethod
    async def update(
        cls,
        db: Union[Database, Path, str],
        where: Dict[str, Any],
        fields: Dict[str, Any]
    ) -> None:
        db_conn = db if isinstance(db, Database) else Database(db)
        auto_updates = {k: 'CURRENT_TIMESTAMP' for k, f in cls._fields.items() if f.on_update}

        set_parts = []
        vals: List[Any] = []
        for k, v in fields.items():
            if k in cls._fields:
                set_parts.append(f"{k} = ?")
                vals.append(v)
        for k, expr in auto_updates.items():
            set_parts.append(f"{k} = {expr}")
        
        if not set_parts:
            # Nothing to do
            return
        
        set_sql = ", ".join(set_parts)
        where_sql = " AND ".join(f"{k} = ?" for k in where.keys())
        vals.extend(where.values())
        sql = f"UPDATE {cls.__tablename__} SET {set_sql} WHERE {where_sql}"

        await db_conn.execute(sql, tuple(vals))
        await db_conn.commit()

    @classmethod
    async def delete(
        cls,
        db: Union[Database, Path, str],
        where: Dict[str, Any]
    ) -> None:
        db_conn = db if isinstance(db, Database) else Database(db)
        where_sql = " AND ".join(f"{k} = ?" for k in where.keys())
        vals = list(where.values())
        sql = f"DELETE FROM {cls.__tablename__} WHERE {where_sql}"
        await db_conn.execute(sql, tuple(vals))
        await db_conn.commit()

    @classmethod
    async def get_or_create(
        cls,
        db: Union[Database, Path, str],
        defaults: Dict[str, Any] = None,
        **kwargs: Any
    ) -> Tuple[Dict[str, Any], bool]:
        db_conn = db if isinstance(db, Database) else Database(db)
        existing = await cls.find(db_conn, where=kwargs)
        if existing:
            return existing[0], False
        params = {**kwargs, **(defaults or {})}
        await cls.insert_or_ignore(db_conn, **params)
        created = await cls.find(db_conn, where=kwargs)
        if created:
            return created[0], True
        # If still missing, it means the row existed but was filtered; return that.
        fallback = await cls.find(db_conn, where=kwargs)
        return fallback[0], False
    
    @classmethod
    async def exists(
        cls,
        db: Union[Database, Path, str],
        where: Dict[str, Any] = None
    ) -> bool:
        """
        Fast existence check. Returns True if at least one row matches `where`, else False.
        Mirrors `find`'s operator-suffix behavior.
        """
        db_conn = db if isinstance(db, Database) else Database(db)
        sql = f"SELECT 1 FROM {cls.__tablename__}"
        params: List[Any] = []

        if where:
            # Validate field names (like `find`)
            invalid_fields = [
                k.split('__', 1)[0] for k in where.keys()
                if k.split('__', 1)[0] not in cls._fields
            ]
            if invalid_fields:
                raise ValueError(f"Unknown fields for {cls.__name__}: {invalid_fields}")

            conditions = []
            for key, val in where.items():
                if '__' in key:
                    fname, op = key.split('__', 1)
                    sql_op = _OPERATOR_MAP.get(op)
                    if sql_op == 'IN' and isinstance(val, (list, tuple)):
                        placeholders = ",".join("?" for _ in val)
                        conditions.append(f"{fname} IN ({placeholders})")
                        params.extend(val)
                    elif sql_op == 'NOT IN' and isinstance(val, (list, tuple)):
                        placeholders = ",".join("?" for _ in val)
                        conditions.append(f"{fname} NOT IN ({placeholders})")
                        params.extend(val)
                    elif sql_op:
                        conditions.append(f"{fname} {sql_op} ?")
                        params.append(val)
                    else:
                        # Fall back to literal key equality, matching `find`'s behavior
                        conditions.append(f"{key} = ?")
                        params.append(val)
                else:
                    conditions.append(f"{key} = ?")
                    params.append(val)
            sql += " WHERE " + " AND ".join(conditions)

        sql += " LIMIT 1"
        row = await db_conn.fetchone(sql, tuple(params))
        return row is not None
