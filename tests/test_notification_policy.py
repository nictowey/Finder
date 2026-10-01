"""The alert dispatcher must accept the policy the worker actually emits."""

import re
from pathlib import Path

from finder.watch_worker import POLICY


def test_notification_policy_matches_worker():
    source = Path("scripts/send_watch_notifications.mjs").read_text()
    versions = re.findall(r"private-target-review-v\d+", source)
    assert versions == [POLICY]
    assert re.search(r"export const REVIEW_POLICY = ['\"]" + re.escape(POLICY), source)
    assert "toISOString(),REVIEW_POLICY,new Date()" in source
