import aiosqlite
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --- Field Definition --------------------------------
class Field:
    def __init__(
        self,
        column_type: str,
        primary_key: bool = False,
        default: Any = None,
        check: Optional[str] = None,
        on_update: bool = False
    ):
        self.column_type = column_type
        self.primary_key = primary_key
        self.default = default
        self.check = check
        self.on_update = on_update

# --- Operator mapping --------------------------------
_OPERATOR_MAP = {
    'gt': '>',
    'lt': '<',
    'gte': '>=',
    'lte': '<=',
    'ne': '!=',
    'in': 'IN'
}

# --- Model Metaclass --------------------------------
class ModelMeta(type):
    def __init__(cls, name, bases, attrs):
        if name == 'Model':
            return
        # Table name
        cls.__tablename__ = getattr(cls, '__tablename__', cls.__name__.lower())
        # Collect fields
        annotations = attrs.get('__annotations__', {})
        cls._fields: Dict[str, Field] = {}
        for key, annotation in annotations.items():
            field = attrs.get(key)
            if isinstance(field, Field):
                cls._fields[key] = field
        # Build CREATE TABLE SQL
        cols: List[str] = []
        for fname, fld in cls._fields.items():
            col_def = f"{fname} {fld.column_type}"
            if fld.primary_key:
                col_def += ' PRIMARY KEY'
            if fld.default is not None:
                default_val = f"'{fld.default}'" if isinstance(fld.default, str) else fld.default
                col_def += f' DEFAULT {default_val}'
            if fld.check:
                col_def += f' CHECK({fld.check})'
            cols.append(col_def)
        cls._create_sql = f"""
            CREATE TABLE IF NOT EXISTS {cls.__tablename__} (
                {', '.join(cols)}
            )
        """

    async def create_table(cls, db_path: Path) -> None:
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(cls._create_sql)
            await db.commit()

    async def create_index(
        cls,
        db_path: Path,
        name: str,
        columns: List[str],
        unique: bool = False
    ) -> None:
        cols = ", ".join(columns)
        uq = 'UNIQUE ' if unique else ''
        sql = f"CREATE {uq}INDEX IF NOT EXISTS {name} ON {cls.__tablename__}({cols})"
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(sql)
            await db.commit()

# --- Base Model --------------------------------------
class Model(metaclass=ModelMeta):
    @classmethod
    async def insert(
        cls,
        db_path: Path,
        **kwargs: Any
    ) -> int:
        keys, vals = zip(*[(k, v) for k, v in kwargs.items() if k in cls._fields])
        cols = ", ".join(keys)
        ph = ", ".join("?" for _ in keys)
        sql = f"INSERT INTO {cls.__tablename__}({cols}) VALUES ({ph})"
        async with aiosqlite.connect(str(db_path)) as db:
            cur = await db.execute(sql, vals)
            await db.commit()
            return cur.lastrowid

    @classmethod
    async def insert_or_ignore(
        cls,
        db_path: Path,
        **kwargs: Any
    ) -> Optional[int]:
        keys, vals = zip(*[(k, v) for k, v in kwargs.items() if k in cls._fields])
        cols = ", ".join(keys)
        ph = ", ".join("?" for _ in keys)
        sql = (
            f"INSERT OR IGNORE INTO {cls.__tablename__}({cols}) "
            f"VALUES ({ph})"
        )
        async with aiosqlite.connect(str(db_path)) as db:
            cur = await db.execute(sql, vals)
            await db.commit()
            return cur.lastrowid if cur.rowcount else None

    @classmethod
    async def filter(
        cls,
        db_path: Path,
        filter: Dict[str, Any] = None,
        fields: List[str] = None,
        order_by: str = None
    ) -> List[Dict[str, Any]]:
        cols = fields or list(cls._fields.keys())
        col_sql = ", ".join(cols)
        sql = f"SELECT {col_sql} FROM {cls.__tablename__}"
        params: List[Any] = []
        if filter:
            where = []
            for key, val in filter.items():
                if '__' in key:
                    fname, op = key.split('__', 1)
                    sql_op = _OPERATOR_MAP.get(op)
                    if sql_op == 'IN' and isinstance(val, (list, tuple)):
                        placeholders = ",".join("?" for _ in val)
                        where.append(f"{fname} IN ({placeholders})")
                        params.extend(val)
                    elif sql_op:
                        where.append(f"{fname} {sql_op} ?")
                        params.append(val)
                    else:
                        # fallback to equality
                        where.append(f"{key} = ?")
                        params.append(val)
                else:
                    where.append(f"{key} = ?")
                    params.append(val)
            sql += " WHERE " + " AND ".join(where)
        if order_by:
            sql += f" ORDER BY {order_by}"
        async with aiosqlite.connect(str(db_path)) as db:
            cur = await db.execute(sql, params)
            rows = await cur.fetchall()
        return [dict(zip(cols, row)) for row in rows]

    @classmethod
    async def update(
        cls,
        db_path: Path,
        where: Dict[str, Any],
        fields: Dict[str, Any]
    ) -> None:
        # handle on_update timestamp fields
        auto_updates = {k: 'CURRENT_TIMESTAMP' for k, f in cls._fields.items() if f.on_update}
        # build SET clause
        set_parts = []
        vals = []
        for k, v in fields.items():
            if k in cls._fields:
                set_parts.append(f"{k} = ?")
                vals.append(v)
        for k, v in auto_updates.items():
            set_parts.append(f"{k} = {v}")
        set_sql = ", ".join(set_parts)
        # build WHERE clause
        where_sql = " AND ".join(f"{k} = ?" for k in where.keys())
        vals.extend(where.values())
        sql = f"UPDATE {cls.__tablename__} SET {set_sql} WHERE {where_sql}"
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(sql, vals)
            await db.commit()

    @classmethod
    async def delete(
        cls,
        db_path: Path,
        filter: Dict[str, Any]
    ) -> None:
        where_sql = " AND ".join(f"{k} = ?" for k in filter.keys())
        vals = list(filter.values())
        sql = f"DELETE FROM {cls.__tablename__} WHERE {where_sql}"
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(sql, vals)
            await db.commit()

    @classmethod
    async def get_or_create(
        cls,
        db_path: Path,
        defaults: Dict[str, Any] = None,
        **kwargs: Any
    ) -> Tuple[Dict[str, Any], bool]:
        rows = await cls.filter(db_path, filter=kwargs)
        if rows:
            return rows[0], False
        params = {**kwargs, **(defaults or {})}
        await cls.insert(db_path, **params)
        return params, True
