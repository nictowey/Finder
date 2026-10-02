"""JSON field reads with a narrow fallback for PostgreSQL escape restrictions."""

from sqlalchemy import JSON, String, case, cast, func, null, type_coerce


def json_field_projection(column, path, dialect_name):
    """Return a JSON field and an exceptional full document, never both.

    PostgreSQL's JSON operators can reject escaped NUL or unpaired surrogates even
    in an ignored property. Reading the original JSON document into Python did not.
    Guard extraction using raw text and retain that decoder for these documents.
    Valid surrogate pairs also take this conservative fallback. SQLite extraction
    truncates NUL strings and can emit invalid UTF-8 for unpaired surrogates, so it
    uses the same fallback.
    """
    field = column[path]
    empty = type_coerce(null(), JSON)
    raw = cast(column, String)
    exceptional = (
        raw.bool_op("~")(r"\\u(0000|[dD][89a-fA-F][0-9a-fA-F]{2})")
        if dialect_name == "postgresql"
        else raw.bool_op("GLOB")(r"*\u0000*")
        | raw.bool_op("GLOB")(r"*\u[dD][89a-fA-F][0-9a-fA-F][0-9a-fA-F]*")
    )
    kind = func.json_typeof if dialect_name == "postgresql" else func.json_type
    # Preserve Python mapping access (including its errors) for malformed roots.
    exceptional = exceptional | (kind(column) != "object")
    return case((exceptional, empty), else_=field), case((exceptional, column), else_=empty)
