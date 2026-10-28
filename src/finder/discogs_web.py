"""Loopback-only private Discogs rule editor. No live discovery or checkout."""

import argparse
import os
import secrets
import sys
from contextlib import nullcontext
from pathlib import Path

from finder.discogs_change_outbox import InAppChangeOutbox
from finder.discogs_checkout_readiness import checkout_readiness
from finder.discogs_hosted_scheduler import HostedReleaseScheduler
from finder.discogs_listing import ListingReader, ListingReadError, observation
from finder.discogs_rules import RulesStore, release_id
from finder.discogs_ui import render_page
from finder.local_web import LocalReviewHandler, LocalReviewServer, local_boundary
from finder.offline import _check_environment


class DiscogsHandler(LocalReviewHandler):
    def _outbox(self):
        database = Path(self.server.workspace.db.execute("PRAGMA database_list").fetchone()[2])
        return InAppChangeOutbox(database)

    def _control_revision(self):
        store = self.server.workspace.db
        if not store.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cloud_control'"
        ).fetchone():
            return None
        row = store.execute("SELECT revision FROM cloud_control WHERE id=1").fetchone()
        return row[0] if row else None

    def do_GET(self):
        if self.headers.get_all("Content-Length", []) not in ([], ["0"]):
            self.send_error(400)
            return
        if self.path == "/":
            nonce = secrets.token_urlsafe(24)
            self._send(
                200,
                render_page(self.server.csrf_token, nonce),
                "text/html; charset=utf-8",
                nonce=nonce,
            )
        elif self.path == "/api/snapshot":
            saved = self.server.workspace.snapshot()
            control_revision = self._control_revision()
            outbox = self._outbox()
            try:
                outbox.materialize(
                    expected_rules_revision=saved["revision"],
                    expected_control_revision=control_revision,
                )
            except RuntimeError:
                self._json(409, {"error": "State changed during review. Refresh to try again."})
                return
            self._json(
                200,
                {
                    **saved,
                    "in_app_alerts": {
                        "channel": "in_app_only",
                        "messages_sent": 0,
                        "purchase_allowed": False,
                        "control_revision": control_revision,
                        "pending": outbox.pending(),
                    },
                    "capabilities": {
                        "listing_read_enabled": getattr(self.server, "listing_reader", None)
                        is not None,
                        "order_authorization_configured": False,
                        "order_submission_enabled": False,
                        "order_reconciliation_configured": False,
                    },
                    "checkout_readiness": checkout_readiness(
                        getattr(self.server, "checkout_status_path", None)
                    ),
                    "hosted_schedule": self.server.hosted_schedule.health()
                    if getattr(self.server, "hosted_schedule", None) is not None
                    else {"paused": True, "targets": [], "purchase_allowed": False},
                },
            )
        else:
            self._json(404, {"error": "Unknown route."})

    def do_POST(self):
        data = self._body()
        if data is None:
            return
        try:
            if self.path == "/api/target" and set(data) == {"target", "expected_revision"}:
                result = self.server.workspace.save(data["target"], data["expected_revision"])
            elif self.path == "/api/evaluate" and set(data) == {"release_id", "observation"}:
                if (
                    not isinstance(data["observation"], dict)
                    or data["observation"].get("source") != "synthetic"
                ):
                    raise ValueError("Synthetic input required.")
                result = self.server.workspace.review(data["release_id"], data["observation"])
            elif self.path == "/api/listing" and set(data) == {"release_id", "listing"}:
                reader = getattr(self.server, "listing_reader", None)
                if reader is None:
                    self._json(
                        403,
                        {
                            "error": "Live listing reads are disabled. "
                            "Start with --live-listing-read to opt in."
                        },
                    )
                    return
                if type(data["release_id"]) is not int or not any(
                    t["release_id"] == data["release_id"]
                    for t in self.server.workspace.snapshot()["targets"]
                ):
                    raise ValueError("Save a target first.")
                listing = reader.read_for_release(data["listing"], data["release_id"])
                evaluation = self.server.workspace.review(
                    data["release_id"], observation(listing).model_dump()
                )
                if listing["shipping_blocked"]:
                    evaluation["reasons"].append("Shipping availability is blocked or unverified.")
                result = {
                    "listing": listing,
                    "evaluation": evaluation,
                    "warning": "Reported shipping is not a final destination quote; "
                    "tax, fees and physical pressing are unverified. Purchasing is unavailable.",
                }
            elif self.path == "/api/owned" and set(data) == {
                "release_id",
                "owned",
                "expected_revision",
            }:
                result = self.server.workspace.mark_owned(**data)
            elif self.path == "/api/change-alert/ack" and set(data) == {
                "change_sequences",
                "expected_revision",
                "expected_control_revision",
            }:
                result = self._outbox().acknowledge(
                    data["change_sequences"],
                    expected_rules_revision=data["expected_revision"],
                    expected_control_revision=data["expected_control_revision"],
                )
            elif self.path == "/api/monitor-release" and set(data) == {
                "release_id",
                "enabled",
                "expected_revision",
            }:
                schedule = getattr(self.server, "hosted_schedule", None)
                if schedule is None or schedule.health()["paused"] is not True:
                    self._json(
                        409, {"error": "The read-only schedule must be paused before saving."}
                    )
                    return
                ident = release_id(data["release_id"])
                revision = schedule.set_monitor_release(
                    ident,
                    data["enabled"],
                    expected_rules_revision=data["expected_revision"],
                )
                result = {
                    "release_id": ident,
                    "enabled": data["enabled"],
                    "rules_revision": revision,
                    "hosted_schedule": schedule.health(),
                    "purchase_allowed": False,
                }
            else:
                self._json(404, {"error": "Unknown route or request fields."})
                return
            self._json(200, result)
        except ListingReadError as error:
            self._json(502, {"error": str(error)})
        except ValueError:
            if self.path == "/api/change-alert/ack":
                self._json(400, {"error": "Invalid or expired change. Refresh before reviewing."})
                return
            if self.path == "/api/monitor-release":
                self._json(400, {"error": "Use one exact Discogs release ID or release link."})
                return
            self._json(
                400,
                {
                    "error": "Invalid fields. Check the release link, required settings, "
                    "grades, amounts and synthetic observation format."
                },
            )
        except RuntimeError:
            self._json(
                409,
                {
                    "error": "Saved state changed. Your draft is retained. "
                    "Reload state before explicitly saving again."
                },
            )


class DiscogsServer(LocalReviewServer):
    # A stopped server may leave accepted HTTP connections in TIME_WAIT. Reuse
    # its loopback port on restart without allowing a second live listener.
    allow_reuse_address = True

    def finish_request(self, request, client_address):
        DiscogsHandler(request, client_address, self)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Private Discogs rules: local only, no purchasing")
    parser.add_argument("--database", required=True)
    parser.add_argument("--init", action="store_true", help="Create a new file; never overwrite")
    parser.add_argument(
        "--live-listing-read",
        action="store_true",
        help="Opt into explicit public known-listing API reads; never discovery or purchases",
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--checkout-status",
        type=Path,
        help="Read an owner-only operational status record; never enables checkout",
    )
    args = parser.parse_args(argv)
    try:
        _check_environment(os.environ)
        if not 0 <= args.port <= 65535:
            raise ValueError("Port must be between 0 and 65535.")
        from finder.local_workspace import _path

        database = _path(args.database)
        with nullcontext() if args.live_listing_read else local_boundary(database, args.port):
            store = RulesStore(Path(database), create=args.init)
            try:
                with DiscogsServer(store, args.port) as server:
                    # Status only: no browser socket, worker loop or live reader is configured.
                    server.hosted_schedule = HostedReleaseScheduler(Path(database), reader=None)
                    server.listing_reader = ListingReader() if args.live_listing_read else None
                    server.checkout_status_path = args.checkout_status
                    print(f"Sniper private Discogs rules: {server.origin}", flush=True)
                    print(
                        "Purchasing disabled. Explicit known-listing reads enabled."
                        if args.live_listing_read
                        else "Purchasing and provider access disabled. Press Ctrl+C to stop.",
                        flush=True,
                    )
                    try:
                        server.serve_forever(poll_interval=0.25)
                    except KeyboardInterrupt:
                        pass
            finally:
                store.close()
    except Exception:
        print(
            "Cannot open local rules workspace. Check path, file type, environment and port. "
            "Use --init only with a new filename; legacy files are not migrated.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
