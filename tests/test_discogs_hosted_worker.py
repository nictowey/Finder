"""Offline worker and page-coverage tests; no browser or provider access."""

import copy
import threading

import pytest
from test_discogs_hosted_scheduler import page, resume, scheduler
from test_discogs_hosted_scheduler import setup as hosted_setup
from test_discogs_release_snapshots import listing

from finder.discogs_hosted_scheduler import MIN_INTERVAL, HostedReadError
from finder.discogs_hosted_worker import run
from finder.discogs_release_snapshots import assemble_release_pages
from finder.discogs_rules import RulesStore, release_id


@pytest.fixture
def setup(tmp_path):
    return hosted_setup.__wrapped__(tmp_path)


def captured_page(now, index, start, end, total, ids):
    return {
        "source": "discogs_rendered_release",
        "purchase_allowed": False,
        "release_id": 123,
        "url": "https://www.discogs.com/sell/release/123" + (f"?page={index}" if index > 1 else ""),
        "observed_at": page(now)["observed_at"],
        "page": index,
        "range_start": start,
        "range_end": end,
        "total": total,
        "has_next": end < total,
        "rows": [listing(value) for value in ids],
        "assertions": page(now)["assertions"],
    }


def test_page_assembly_requires_full_contiguous_unique_coverage(setup):
    _, clock = setup
    pages = [
        captured_page(clock[0], 1, 1, 2, 3, (1001, 1002)),
        captured_page(clock[0], 2, 3, 3, 3, (1003,)),
    ]
    complete = assemble_release_pages(123, pages)
    assert complete["complete"] is True
    assert complete["coverage_pages"] == 2
    assert [row["listing_id"] for row in complete["rows"]] == ["1001", "1002", "1003"]
    for broken in (
        pages[:1],
        [pages[0], {**pages[1], "range_start": 4}],
        [pages[0], {**pages[1], "release_id": 124}],
        [pages[0], {**pages[1], "rows": [listing(1002)]}],
        [pages[0], {**pages[1], "url": "https://other.example/sell/release/123?page=2"}],
    ):
        with pytest.raises(ValueError):
            assemble_release_pages(123, copy.deepcopy(broken))


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.discogs.com/release/28720177-DJ-Esco-Hosted-By-Future-4-56-Nights", 28720177),
        ("https://www.discogs.com/release/5520534-Schoolboy-Q-Oxymoron", 5520534),
        (
            "https://www.discogs.com/release/13053315-Metro-Boomin-Not-All-Heroes-Wear-Capes",
            13053315,
        ),
    ],
)
def test_user_supplied_release_ids_remain_independent_read_only_inputs(url, expected, setup):
    _, clock = setup
    assert release_id(url) == expected
    item = captured_page(clock[0], 1, 1, 1, 1, (1001,))
    item["release_id"] = expected
    item["url"] = f"https://www.discogs.com/sell/release/{expected}"
    complete = assemble_release_pages(expected, [item])
    assert complete["release_id"] == expected
    assert complete["purchase_allowed"] is False
    with pytest.raises(ValueError):
        assemble_release_pages(123, [item])


def test_worker_exits_without_reader_when_paused_or_stopped(setup, tmp_path):
    database, clock = setup
    assert run(database, tmp_path / "absent.sock", threading.Event())["reason"] == "schedule_paused"
    resume(scheduler(database, clock, lambda _: page(clock[0])))
    assert (
        run(database, tmp_path / "absent.sock", threading.Event())["reason"] == "reader_unavailable"
    )


def test_worker_does_not_repeat_after_restart(setup, monkeypatch, tmp_path):
    database, clock = setup
    reads = []

    class Reader:
        def ready(self):
            return True

        def __call__(self, release):
            reads.append(release)
            return page(clock[0])

    monkeypatch.setattr("finder.discogs_hosted_worker.HostedSocketReader", lambda _: Reader())
    resume(scheduler(database, clock, Reader()))

    class StopAfterWait:
        def __init__(self):
            self.stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, _):
            self.stopped = True

    assert run(database, tmp_path / "socket", StopAfterWait())["reason"] == "signal"
    assert run(database, tmp_path / "socket", StopAfterWait())["reason"] == "signal"
    assert reads == [123]


def test_multiple_exact_release_targets_have_independent_due_slots(setup):
    database, clock = setup
    store = RulesStore(database)
    current = store.snapshot()
    second = copy.deepcopy(current["targets"][0])
    second["release_id"] = 456
    store.save(second, current["revision"])
    store.close()
    seen = []

    def read(release):
        seen.append(release)
        return {
            **page(clock[0]),
            "release_id": release,
            "url": f"https://www.discogs.com/sell/release/{release}",
        }

    worker = scheduler(database, clock, read)
    resume(worker)
    assert worker.tick()["status"] == "baseline"
    assert worker.tick()["status"] == "idle"
    clock[0] += MIN_INTERVAL + 1
    assert scheduler(database, clock, read).tick()["status"] == "baseline"
    assert seen == [123, 456]


def test_access_restriction_pauses_worker_with_explicit_code(setup, monkeypatch, tmp_path):
    database, clock = setup

    class RestrictedReader:
        def ready(self):
            return True

        def __call__(self, _release):
            raise HostedReadError("access_restricted", stop=True)

    monkeypatch.setattr(
        "finder.discogs_hosted_worker.HostedSocketReader", lambda _: RestrictedReader()
    )
    resume(scheduler(database, clock, RestrictedReader()))
    result = run(database, tmp_path / "socket", threading.Event())
    assert result == {"status": "stopped", "reason": "access_restricted", "purchase_allowed": False}
    assert scheduler(database, clock, RestrictedReader()).health()["paused"] is True
