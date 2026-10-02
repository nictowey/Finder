"""Execute the real projection expressions with SQLite and PostgreSQL/WASM."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import (
    JSON,
    String,
    case,
    cast,
    create_engine,
    func,
    literal,
    null,
    select,
    type_coerce,
)
from sqlalchemy.dialects import postgresql

from finder.json_projection import json_field_projection

VALUES = [
    {},
    None,
    {"marker": None},
    {"marker": ""},
    {"marker": "2026-10-02T00:00:00Z"},
    {"marker": False},
    {"marker": True},
    {"marker": 0},
    {"marker": []},
    {"marker": {}},
    {"marker": "before\0after"},
    {"marker": "\ud800"},
    {"marker": "\udc00"},
    {"marker": "\U0001f600"},
    *[{"marker": "same", "ignored": value} for value in ("\0", "\ud800", "\udc00", "\U0001f600")],
    {"marker": "same", "ignored": "\\u0000"},
]


def projection_queries(dialect_name):
    """Text inputs retain escaped Unicode rather than relying on driver rewriting."""
    for value in VALUES:
        raw = literal(json.dumps(value), type_=String)
        # SQLite's JSON bind processor serializes Python values; PG receives raw text.
        source = cast(raw, JSON) if dialect_name == "postgresql" else literal(value, type_=JSON)
        field, fallback = json_field_projection(source, ("marker",), dialect_name)
        kind = func.json_typeof if dialect_name == "postgresql" else func.json_type
        yield select(field.label("field"), fallback.label("fallback"), kind(source).label("kind"))
    # Cadence's CASE must not evaluate json_array_length for a nonarray or unsafe JSON.
    for value in ([], [1, 2, 3], None, "Album", {"x": 1}, False):
        raw = literal(json.dumps({"queries": value}), type_=String)
        source = (
            cast(raw, JSON)
            if dialect_name == "postgresql"
            else literal({"queries": value}, type_=JSON)
        )
        field, fallback = json_field_projection(source, ("queries",), dialect_name)
        kind = func.json_typeof if dialect_name == "postgresql" else func.json_type
        array = kind(field) == "array"
        yield select(
            case((array, func.json_array_length(field))).label("count"),
            case((array, None), else_=field).label("other"),
            fallback.label("fallback"),
        )

    source = cast(null(), JSON) if dialect_name == "postgresql" else type_coerce(null(), JSON)
    field, fallback = json_field_projection(source, ("marker",), dialect_name)
    yield select(field.label("field"), fallback.label("fallback"), kind(source).label("kind"))


def check_results(rows):
    for value, row in zip(VALUES, rows, strict=False):
        actual = row["fallback"].get("marker") if row["fallback"] is not None else row["field"]
        assert actual == (value or {}).get("marker")
        if "\\u0000" in json.dumps(value) or "\\ud" in json.dumps(value):
            assert row["fallback"] == value and row["field"] is None
        else:
            assert row["fallback"] is None
        assert row["kind"] == ("null" if value is None else "object")
    assert rows[len(VALUES) :] == [
        {"count": 0, "other": None, "fallback": None},
        {"count": 3, "other": None, "fallback": None},
        {"count": None, "other": None, "fallback": None},
        {"count": None, "other": "Album", "fallback": None},
        {"count": None, "other": {"x": 1}, "fallback": None},
        {"count": None, "other": False, "fallback": None},
        {"field": None, "fallback": None, "kind": None},
    ]


def test_sqlite_preserves_json_values_and_escaped_unicode():
    with create_engine("sqlite://").connect() as conn:
        check_results(
            [dict(conn.execute(query).mappings().one()) for query in projection_queries("sqlite")]
        )


@pytest.mark.skipif(
    not shutil.which("node") or not Path("node_modules/@electric-sql/pglite").exists(),
    reason="PostgreSQL expression execution needs local Node/PGlite dependencies",
)
def test_postgres_preserves_json_values_and_escaped_unicode():
    queries = [
        str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        for query in projection_queries("postgresql")
    ]
    result = subprocess.run(
        ["node", "--import", "tsx", "tests/support/worker_projection_postgres.ts"],
        input=json.dumps({"queries": queries}),
        capture_output=True,
        text=True,
        check=True,
    )
    check_results([rows[0] for rows in json.loads(result.stdout)])
