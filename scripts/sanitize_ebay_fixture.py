"""Convert a captured eBay response bundle into a safe replay fixture."""

import argparse
import json
from pathlib import Path

from finder.adapters.ebay.replay import sanitize_fixture


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    bundle = json.loads(args.input.read_text(encoding="utf-8"))
    sanitized = sanitize_fixture(bundle)
    args.output.write_text(json.dumps(sanitized, indent=2) + "\n", encoding="utf-8")
    print(f"Sanitized fixture written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
