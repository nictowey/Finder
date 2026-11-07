"""Opt-in app-only authentication for public aggregate stats, never user credentials."""

import json
import os
import re
import stat

from finder.discogs_listing import ListingReadError, ReleaseStatsReader
from finder.discogs_rules import CURRENCIES

_ERROR = "Private Discogs application configuration unavailable."


class AppCredentials:
    """Read only an explicitly supplied existing protected file; never repair it."""

    def __init__(self, path):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)
                    or info.st_nlink != 1
                ):
                    raise ValueError(_ERROR)
                raw = source.read(1025)
            if len(raw) > 1024:
                raise ValueError(_ERROR)
            data = json.loads(raw)
            if (
                not isinstance(data, dict)
                or set(data) != {"consumer_key", "consumer_secret"}
                or not all(
                    isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value)
                    for value in data.values()
                )
            ):
                raise ValueError(_ERROR)
            self._header = f"Discogs key={data['consumer_key']}, secret={data['consumer_secret']}"
        except (OSError, ValueError, UnicodeError, RecursionError):
            raise ValueError(_ERROR) from None

    def __repr__(self):
        return "AppCredentials(<redacted>)"


class AuthenticatedReleaseStatsReader(ReleaseStatsReader):
    """App identity only; fixed public stats route, no redirects or ambient proxies."""

    _trust_env = False

    def __init__(self, credentials, **kwargs):
        if not isinstance(credentials, AppCredentials):
            raise ValueError(_ERROR)
        super().__init__(**kwargs)
        self._credentials = credentials

    def _request_headers(self, path, params):
        # Guard even inherited listing/catalog methods: no expansion of auth scope.
        if (
            not re.fullmatch(r"/marketplace/stats/[1-9][0-9]{0,15}", path)
            or not isinstance(params, dict)
            or set(params) != {"curr_abbr"}
            or params["curr_abbr"] not in CURRENCIES
        ):
            raise ListingReadError("Authenticated reader permits only public release statistics.")
        return {
            **super()._request_headers(path, params),
            "Authorization": self._credentials._header,
        }
