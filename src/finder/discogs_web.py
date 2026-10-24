"""Loopback-only private Discogs rule editor. No live discovery or checkout."""

import argparse
import os
import secrets
import sys
from pathlib import Path

from finder.discogs_rules import RulesStore
from finder.discogs_ui import render_page
from finder.local_web import LocalReviewHandler, LocalReviewServer, local_boundary
from finder.offline import _check_environment


class DiscogsHandler(LocalReviewHandler):
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
            self._json(200, self.server.workspace.snapshot())
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
                result = self.server.workspace.review(data["release_id"], data["observation"])
            elif self.path == "/api/owned" and set(data) == {
                "release_id",
                "owned",
                "expected_revision",
            }:
                result = self.server.workspace.mark_owned(**data)
            else:
                self._json(404, {"error": "Unknown route or request fields."})
                return
            self._json(200, result)
        except ValueError:
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
    def finish_request(self, request, client_address):
        DiscogsHandler(request, client_address, self)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Private Discogs rules: local only, no purchasing")
    parser.add_argument("--database", required=True)
    parser.add_argument("--init", action="store_true", help="Create a new file; never overwrite")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        _check_environment(os.environ)
        if not 0 <= args.port <= 65535:
            raise ValueError("Port must be between 0 and 65535.")
        from finder.local_workspace import _path

        database = _path(args.database)
        with local_boundary(database, args.port):
            store = RulesStore(Path(database), create=args.init)
            try:
                with DiscogsServer(store, args.port) as server:
                    print(f"Finder private Discogs rules: {server.origin}", flush=True)
                    print(
                        "Purchasing and provider access disabled. Press Ctrl+C to stop.", flush=True
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
