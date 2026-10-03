"""Render the local UI and one synthetic snapshot for the separate offline DOM check."""

import argparse
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from finder.local_ui import render_page
from finder.offline import _check_environment, _offline_boundary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()
    _check_environment(os.environ)
    args.output_directory.mkdir(parents=True, exist_ok=True)
    html = render_page("dom-test-token", "test-nonce")
    with tempfile.TemporaryDirectory(prefix="finder-local-dom-") as directory:
        database = (Path(directory) / "review.sqlite3").resolve()
        with _offline_boundary(database):
            from finder.local_workspace import LocalWorkspace

            with LocalWorkspace.create(database) as workspace:
                observed = datetime.now(UTC).isoformat()
                snapshot = workspace.import_bundle(
                    {
                        "schema_version": 1,
                        "source": "synthetic",
                        "target": {
                            "artist": "Example Ensemble",
                            "album": "Offline Horizons",
                            "colors": ["Blue"],
                        },
                        "settings": {
                            "maximum_subtotal": "30.00",
                            "gamble_max": "15.00",
                            "currency": "USD",
                        },
                        "listings": [
                            {
                                "id": "hostile-text",
                                "title": "Example Ensemble Offline Horizons "
                                "<img src=x onerror=alert(1)> blue vinyl",
                                "observed_at": observed,
                                "details_observed_at": observed,
                                "current_price": "10.00",
                                "shipping_cost": "4.00",
                                "currency": "USD",
                                "shipping_currency": "USD",
                                "price_kind": "fixed_price",
                            }
                        ],
                    }
                )
    (args.output_directory / "index.html").write_text(html, encoding="utf-8")
    (args.output_directory / "snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")


if __name__ == "__main__":
    main()
