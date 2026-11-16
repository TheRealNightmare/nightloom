"""The Neo4j schema: constraints and indexes for entity nodes.

Apply it with ``python -m nightloom_api.graph.schema apply`` (the Compose
``migrate`` service and ``make migrate`` do this after the Alembic
migrations). Every statement uses ``IF NOT EXISTS``, so applying it again
changes nothing.

Uniqueness is a composite node property uniqueness constraint on
``(case_id, key)`` for ``:Entity``, which Neo4j Community Edition supports
(node *key* and property *existence* constraints are Enterprise-only, so the
properties are guaranteed present by the code that writes them instead).
The constraint is backed by a range index, which also serves the
``MERGE``/``MATCH`` lookups by ``(case_id, key)``.
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from neo4j import AsyncDriver
from neo4j.exceptions import DriverError, Neo4jError

from nightloom_api.config import Settings, get_settings
from nightloom_api.infra import graph
from nightloom_api.infra.errors import describe_error

logger = logging.getLogger(__name__)

ENTITY_LABEL = "Entity"
CONSTRAINT_NAME = "entity_case_key_unique"
CASE_INDEX_NAME = "entity_case_id"
CASE_TYPE_INDEX_NAME = "entity_case_type"


@dataclass(frozen=True)
class SchemaItem:
    name: str
    statement: str


SCHEMA: tuple[SchemaItem, ...] = (
    SchemaItem(
        CONSTRAINT_NAME,
        f"CREATE CONSTRAINT {CONSTRAINT_NAME} IF NOT EXISTS "
        f"FOR (n:{ENTITY_LABEL}) REQUIRE (n.case_id, n.key) IS UNIQUE",
    ),
    # Whole-case reads and deletion.
    SchemaItem(
        CASE_INDEX_NAME,
        f"CREATE INDEX {CASE_INDEX_NAME} IF NOT EXISTS FOR (n:{ENTITY_LABEL}) ON (n.case_id)",
    ),
    # GET /graph?types=...
    SchemaItem(
        CASE_TYPE_INDEX_NAME,
        f"CREATE INDEX {CASE_TYPE_INDEX_NAME} IF NOT EXISTS "
        f"FOR (n:{ENTITY_LABEL}) ON (n.case_id, n.type)",
    ),
)

# Seconds `apply` waits for new indexes to come online.
INDEX_WAIT_SECONDS = 120


async def apply_schema(driver: AsyncDriver) -> list[str]:
    """Create every missing constraint and index, then wait until they are online.

    Schema changes cannot share a transaction with each other, so each runs
    in its own. Returns the names of the schema items, in order.
    """
    async with driver.session() as session:
        for item in SCHEMA:
            result = await session.run(item.statement)
            await result.consume()
        result = await session.run("CALL db.awaitIndexes($seconds)", seconds=INDEX_WAIT_SECONDS)
        await result.consume()
    return [item.name for item in SCHEMA]


async def describe_schema(driver: AsyncDriver) -> list[dict[str, object]]:
    """The constraints and indexes on ``:Entity`` as plain dictionaries."""
    rows: list[dict[str, object]] = []
    async with driver.session() as session:
        for command, kind in (("SHOW CONSTRAINTS", "constraint"), ("SHOW INDEXES", "index")):
            result = await session.run(
                command + " YIELD name, type, labelsOrTypes, properties "
                "WHERE $label IN coalesce(labelsOrTypes, []) "
                "RETURN name, type, labelsOrTypes, properties ORDER BY name",
                label=ENTITY_LABEL,
            )
            rows.extend([{"kind": kind, **record.data()} async for record in result])
    return rows


async def wait_for_neo4j(driver: AsyncDriver, *, seconds: float, interval: float = 2.0) -> None:
    """Retry until Neo4j accepts a query (it can lag behind its health check after a start)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while True:
        try:
            await graph.ping(driver)
            return
        except (DriverError, Neo4jError, OSError):
            if loop.time() >= deadline:
                raise
            await asyncio.sleep(interval)


async def run(command: str, settings: Settings, *, wait: float) -> list[str]:
    driver = graph.create_driver(settings)
    try:
        await wait_for_neo4j(driver, seconds=wait)
        if command == "apply":
            names = await apply_schema(driver)
            return [f"Applied the Neo4j schema: {', '.join(names)}."]
        return [
            f"{row['kind']} {row['name']}: {row['type']} on {row['labelsOrTypes']} "
            f"{row['properties']}"
            for row in await describe_schema(driver)
        ]
    finally:
        await driver.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m nightloom_api.graph.schema",
        description="Manage the Neo4j schema (constraints and indexes for entity nodes).",
    )
    parser.add_argument("command", choices=["apply", "show"])
    parser.add_argument(
        "--wait",
        type=float,
        default=60.0,
        help="seconds to keep retrying while Neo4j is not reachable yet (default 60)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    try:
        lines = asyncio.run(run(args.command, settings, wait=args.wait))
    except (DriverError, Neo4jError, OSError) as exc:
        message = describe_error(exc, settings.secret_values())
        print(f"Neo4j schema {args.command} failed: {message}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
