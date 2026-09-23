"""Shared fail-closed Browse debits, including retries and other Finder consumers."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import JSON, Column, MetaData, String, Table, create_engine, insert, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from finder.errors import RateLimitError

metadata = MetaData()
budget = Table(
    "finder_browse_budget",
    metadata,
    Column("key", String(32), primary_key=True),
    Column("data", JSON, nullable=False),
)
RESERVE = 200


class BrowseBudget:
    def __init__(self, url, telemetry):
        parsed = make_url(url)
        if parsed.get_backend_name() not in ("postgres", "postgresql"):
            raise RateLimitError("Production Browse requires shared quota storage")
        self.engine = create_engine(
            parsed.set(drivername="postgresql+psycopg"),
            hide_parameters=True,
            pool_size=1,
            max_overflow=0,
        )
        self.telemetry = telemetry
        self.last = {}
        budget.create(self.engine, checkfirst=True)
        try:
            with self.engine.begin() as conn:
                if not conn.execute(select(budget)).first():
                    conn.execute(insert(budget).values(key="browse", data={}))
        except IntegrityError:
            pass

    def close(self):
        self.engine.dispose()

    def debit(self, now=None):
        now = now or datetime.now(UTC)
        with self.engine.begin() as conn:
            saved = conn.execute(
                select(budget.c.data).where(budget.c.key == "browse").with_for_update()
            ).scalar_one()
            data = reconcile(saved, self.telemetry, now)
            if min(r["remaining"] for r in data["rates"]) <= RESERVE:
                raise RateLimitError("Shared Browse safety reserve reached")
            for rate in data["rates"]:
                rate["remaining"] -= 1
            data["debited"] = data.get("debited", 0) + 1
            conn.execute(update(budget).where(budget.c.key == "browse").values(data=data))
            self.last = data


def reconcile(saved, telemetry, now):
    # A reset requires fresh provider evidence, never a local midnight assumption.
    if (
        saved
        and now - datetime.fromisoformat(saved["observed_at"]) < timedelta(minutes=5)
        and all(datetime.fromisoformat(r["reset"]) > now for r in saved["rates"])
    ):
        return saved
    try:
        payload = telemetry()
        pools = [
            resource
            for api in payload["rateLimits"]
            if api.get("apiContext") == "buy" and api.get("apiName") == "browse"
            for resource in api["resources"]
            if resource.get("name") == "buy.browse"
        ]
        if len(pools) != 1 or not pools[0]["rates"]:
            raise ValueError()
        rates = []
        for raw in pools[0]["rates"]:
            remaining, limit, window = raw["remaining"], raw["limit"], raw["timeWindow"]
            reset = datetime.fromisoformat(raw["reset"].replace("Z", "+00:00"))
            if (
                any(type(x) is not int for x in (remaining, limit, window))
                or not 0 <= remaining <= limit
                or reset <= now
            ):
                raise ValueError()
            old = next((r for r in saved.get("rates", []) if r["window"] == window), None)
            if old and datetime.fromisoformat(old["reset"]) > now:
                remaining = min(remaining, old["remaining"])
            rates.append(
                {
                    "remaining": remaining,
                    "limit": limit,
                    "window": window,
                    "reset": reset.isoformat(),
                }
            )
        return {"observed_at": now.isoformat(), "rates": rates, "debited": saved.get("debited", 0)}
    except Exception:
        raise RateLimitError("Shared Browse telemetry unavailable") from None
