#!/usr/bin/env python3
"""Fixed metadata-only bridge to the existing real-order SQLite fence.

No browser, payment method, credentials, or network calls. The production path
is fixed and opening it never creates a ledger. An explicit initialize command
is the only creation path; it is not called by the dormant JS adapter.
"""

import argparse
import importlib.util
import json
import os
import stat
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path

LEDGER = Path("/bridge/discogs-real-orders.sqlite3")
SOURCE = Path(__file__).resolve().parent.parent / "src/finder/discogs_order_reconciliation.py"
MAX_BYTES = 4096
EXPECTED_KEYS = frozenset(
    {
        "intent_id",
        "approval_ref",
        "release_id",
        "listing_id",
        "seller",
        "quoted_total",
        "currency",
        "quote_fingerprint",
        "rules_revision",
        "destination_ref",
        "payment_ref",
        "quote_expires_at",
    }
)
EVIDENCE_KEYS = frozenset(
    {
        "order_id",
        "release_id",
        "listing_id",
        "seller",
        "total",
        "currency",
        "displayed_status",
        "observed_at",
        "source",
    }
)


def _module():
    spec = importlib.util.spec_from_file_location("_fixed_discogs_order_reconciliation", SOURCE)
    if spec is None or spec.loader is None:
        raise ValueError("fixed_module_unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _pairs(entries):
    value = {}
    for key, item in entries:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = item
    return value


def _read():
    data = sys.stdin.buffer.read(MAX_BYTES + 1)
    if not data or len(data) > MAX_BYTES:
        raise ValueError("invalid_request_size")
    value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs)
    if type(value) is not dict or type(value.get("op")) is not str:
        raise ValueError("invalid_request")
    return value


def _parent(path):
    parent = path.parent
    info = parent.lstat()
    mode = stat.S_IMODE(info.st_mode)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or mode not in {0o700, 0o2710}:
        raise ValueError("private_ledger_directory_required")


def _ledger_path(args):
    if args.test_ledger is None:
        return LEDGER
    if os.environ.get("SNIPER_LEDGER_FIXTURE_TEST") != "1":
        raise ValueError("fixture_context_required")
    path = Path(args.test_ledger)
    if not path.is_absolute() or not path.resolve().is_relative_to(
        Path(tempfile.gettempdir()).resolve()
    ):
        raise ValueError("fixture_path_required")
    return path


def _expected(module, value):
    if type(value) is not dict or value.keys() != EXPECTED_KEYS:
        raise ValueError("exact_expected_order_required")
    expected = module.ExpectedOrder(**value)
    if (
        expected.release_id != 3422100
        or expected.listing_id != "1366950910"
        or expected.seller != "Mailman21842"
        or expected.currency != "USD"
        or Decimal(expected.quoted_total) > Decimal("5.05")
    ):
        raise ValueError("exact_kc_order_required")
    return expected


def _evidence(module, value):
    if value is None:
        return None
    if type(value) is not dict or value.keys() != EVIDENCE_KEYS:
        raise ValueError("exact_order_page_evidence_required")
    evidence = module.OrderPageEvidence(**value)
    if (
        evidence.release_id != 3422100
        or evidence.listing_id != "1366950910"
        or evidence.seller.casefold() != "mailman21842"
        or evidence.currency != "USD"
        or Decimal(evidence.total) > Decimal("5.05")
    ):
        raise ValueError("exact_kc_order_evidence_required")
    return evidence


def _run(request, path):
    module = _module()
    operation = request["op"]
    _parent(path)
    if operation == "initialize":
        if request.keys() != {"op"}:
            raise ValueError("invalid_initialize_request")
        module.DiscogsOrderLedger(path, create=True)
        return {"status": "initialized", "purchase_allowed": False}
    ledger = module.DiscogsOrderLedger(path)  # Default is noncreating.
    now = time.time()  # Never use caller-supplied clock to bypass a stale quote.
    if operation in {"prepare", "fence"}:
        if request.keys() != {"op", "expected"}:
            raise ValueError("invalid_order_request")
        expected = _expected(module, request["expected"])
        prepared = ledger.prepare(expected, now=now)
        if operation == "prepare":
            return {"status": "observed", "ledger": prepared, "durable": True}
        if prepared["state"] != "prepared" or prepared["intent_id"] != expected.intent_id:
            raise module.OrderEvidenceStopped("intent_missing_stale_or_already_fenced")
        fenced = ledger.fence_possible_submit(expected.intent_id, now=time.time())
        return {"status": "observed", "ledger": fenced, "durable": True}
    if operation == "status":
        if (
            request.keys() != {"op", "release_id"}
            or type(request["release_id"]) is not int
            or request["release_id"] != 3422100
        ):
            raise ValueError("exact_release_required")
        return {"status": "observed", "ledger": ledger.status_for_release(3422100), "durable": True}
    if operation == "reconcile":
        if request.keys() != {"op", "intent_id", "evidence"}:
            raise ValueError("invalid_reconciliation_request")
        evidence = _evidence(module, request["evidence"])
        result = ledger.record_read_only_lookup(request["intent_id"], evidence, now=now)
        return {"status": "observed", "ledger": result, "durable": True}
    raise ValueError("unsupported_operation")


def main():
    # SQLite may create a rollback journal next to the 0600 ledger.
    os.umask(0o077)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--test-ledger")
    args = parser.parse_args()
    try:
        output = _run(_read(), _ledger_path(args))
    except Exception as error:  # Never print a provider payload, path, or traceback.
        name = type(error).__name__
        code = (
            "ledger_unavailable"
            if name in {"FileNotFoundError", "PermissionError"}
            else (
                "order_slot_or_fence_blocked"
                if name == "OrderEvidenceStopped"
                else "invalid_or_unavailable"
            )
        )
        output = {"status": "blocked", "code": code, "purchase_allowed": False}
    sys.stdout.write(json.dumps(output, sort_keys=True, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
