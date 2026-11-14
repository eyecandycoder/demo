"""Runtime adapters.

The same generated SQL runs on Databricks (Spark SQL) and locally on SQLite for CI tests.
Only two things differ by runtime: how a logical table name becomes a physical one,
and how rows are written.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Optional


class TableNames:
    """Logical 'gold.dim_product' -> physical name for the target runtime."""

    def __init__(self, platform: dict, mode: str = "databricks"):
        self.catalog = platform.get("catalog", "main")
        self.schemas = platform.get("schemas", {})
        self.mode = mode

    def physical(self, logical: str) -> str:
        schema_alias, table = logical.split(".", 1)
        schema = self.schemas.get(schema_alias, schema_alias)
        if self.mode == "sqlite":
            return f"{schema}__{table}"
        return f"{self.catalog}.{schema}.{table}"

    def logical(self, schema_alias: str, table: str) -> str:
        return f"{schema_alias}.{table}"


class SqliteRunner:
    mode = "sqlite"

    def __init__(self, path: str = ":memory:"):
        self.conn = sqlite3.connect(path)

    def query(self, sql: str) -> list[dict[str, Any]]:
        cur = self.conn.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def execute(self, sql: str) -> None:
        self.conn.executescript(sql)

    def table_exists(self, physical: str) -> bool:
        rows = self.query(f"SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name = '{physical}'")
        return bool(rows)

    def write_table(self, physical: str, rows: list[dict], columns: Optional[list[str]] = None) -> None:
        cols = columns or (list(rows[0].keys()) if rows else [])
        if not cols:
            return
        self.conn.execute(f'DROP TABLE IF EXISTS "{physical}"')
        col_list = ", ".join('"' + c + '"' for c in cols)
        self.conn.execute(f'CREATE TABLE "{physical}" ({col_list})')
        self.conn.executemany(
            f'INSERT INTO "{physical}" VALUES ({", ".join("?" for _ in cols)})',
            [tuple(r.get(c) for c in cols) for r in rows],
        )
        self.conn.commit()


class SparkRunner:
    """Used inside Databricks notebooks: SparkRunner(spark)."""
    mode = "databricks"

    def __init__(self, spark):
        self.spark = spark

    def query(self, sql: str) -> list[dict[str, Any]]:
        return [r.asDict() for r in self.spark.sql(sql).collect()]

    def execute(self, sql: str) -> None:
        for stmt in [s for s in sql.split(";\n") if s.strip()]:
            self.spark.sql(stmt)

    def table_exists(self, physical: str) -> bool:
        return self.spark.catalog.tableExists(physical)

    def write_table(self, physical: str, rows: list[dict], columns: Optional[list[str]] = None) -> None:
        if not rows:
            return
        cols = columns or list(rows[0].keys())
        df = self.spark.createDataFrame([tuple(r.get(c) for c in cols) for r in rows], schema=cols)
        df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(physical)
