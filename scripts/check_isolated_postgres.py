"""Run the PostgreSQL rehearsal only against the disposable CI fixture."""

import os

from sqlalchemy.engine import make_url


def validate_fixture_url(value):
    try:
        url = make_url(value)
        allowed = (
            url.drivername == "postgresql"
            and url.host == "127.0.0.1"
            and isinstance(url.port, int)
            and 0 < url.port < 65536
            and url.database == "finder_ci"
            and url.username == "finder_ci"
            and url.password == "finder_ci_ephemeral"
            and not url.query
        )
    except Exception:
        allowed = False
    if not allowed:
        # Never echo a rejected URL: a caller may have supplied real credentials.
        raise ValueError("PostgreSQL rehearsal requires the isolated CI fixture")


def main():
    validate_fixture_url(os.environ.get("FINDER_DATABASE_URL", ""))
    from check_watch_postgres import main as rehearse

    rehearse()


if __name__ == "__main__":
    main()
