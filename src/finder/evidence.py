"""Local freshness boundaries that survive replacement of a provider snapshot."""

from datetime import datetime

INVALIDATED_AT = "finder_details_invalidated_at"
SUMMARY_FINGERPRINT = "finder_summary_fingerprint"


def evidence_time(value):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp if stamp.tzinfo is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def preserve_watermark(data, saved):
    previous = evidence_time(saved.get("source_metadata", {}).get(INVALIDATED_AT))
    incoming = evidence_time(data.get("source_metadata", {}).get(INVALIDATED_AT))
    if previous and (not incoming or previous >= incoming):
        metadata = {
            **data.get("source_metadata", {}),
            INVALIDATED_AT: previous.isoformat(),
        }
        fingerprint = saved.get("source_metadata", {}).get(SUMMARY_FINGERPRINT)
        if fingerprint is not None:
            metadata[SUMMARY_FINGERPRINT] = fingerprint
        else:
            metadata.pop(SUMMARY_FINGERPRINT, None)
        data["source_metadata"] = metadata


def details_invalidated(listing):
    raw = listing.source_metadata.get(INVALIDATED_AT)
    boundary = evidence_time(raw)
    return bool(raw) and (
        not boundary or not listing.details_observed_at or listing.details_observed_at < boundary
    )
