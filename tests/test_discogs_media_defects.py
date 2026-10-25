"""Seller text is evidence of disclosed damage, never proof of physical condition."""

import pytest
from test_discogs_listing import payload
from test_discogs_rules import NOW, observation, target

from finder.discogs_listing import normalize
from finder.discogs_media_defects import assess_media_defects
from finder.discogs_monitor import listing_review
from finder.discogs_rules import evaluate


@pytest.mark.parametrize(
    "comments",
    [
        "Vinyl is warped",
        "slight edge warp on record",
        "disc is dished",
        "record cracked",
        "vinyl chipped",
        "broken LP",
        "disc skips on side A",
        "jumps during playback",
        "stuck groove on disc 2",
        "scratched vinyl",
        "deep scratches on record",
        "minor hairlines on LP",
        "light scuffs on disc",
        "loud surface noise",
        "plays with distortion",
        "persistent crackle",
        "no skips; but jumps on side B",
        "not warped; disc 1 is cracked",
    ],
)
def test_definite_disclosed_media_damage_blocks_review(comments):
    defects = assess_media_defects(comments)
    assert defects["status"] == "defective"
    review = evaluate(target(), observation(seller_comments=comments), now=NOW)
    assert not review["eligible_for_review"]
    assert review["media_defects"]["status"] == "defective"
    assert review["purchase_allowed"] is False


@pytest.mark.parametrize(
    "comments",
    [
        "No skips on either disc",
        "not warped",
        "scratch-free vinyl",
        "no scratches",
        "no loud surface noise",
        "no warping",
        "never skips",
        "cracked case, vinyl plays perfectly",
        "scratched cover",
        "warped sleeve",
        "Record sleeve scuffed",
        "Vinyl VG+ and sleeve cracked",
        "Clean copy",
        "Includes both discs; no scratches or skips",
    ],
)
def test_negations_and_packaging_only_are_not_disclosed_media_damage(comments):
    assert assess_media_defects(comments)["status"] == "no_disclosed_media_defect"


@pytest.mark.parametrize(
    "comments",
    [
        None,
        "possibly warped",
        "might skip",
        "not sure if scratched",
        "scratched",
        "cracked",
        "hairlines",
        "distortion?",
        "maybe scratches on vinyl",
        "record or sleeve scratched",
    ],
)
def test_unknown_or_ambiguous_defect_claims_block_review(comments):
    defects = assess_media_defects(comments)
    assert defects["status"] == "unknown"
    review = evaluate(target(), observation(seller_comments=comments), now=NOW)
    assert not review["eligible_for_review"]
    assert review["media_defects"]["status"] == "unknown"


def test_unsupported_text_cannot_qualify_but_definite_damage_still_rejects():
    assert assess_media_defects("Disque rayé")["status"] == "unknown"
    assert assess_media_defects("vinyl warped <b>")["status"] == "defective"
    assert assess_media_defects("no skips", comments_supported=False)["status"] == "unknown"


def test_known_listing_review_exposes_damage_gate_without_purchase():
    listing = normalize(payload(), 123)
    listing["seller_comments"] = "vinyl warped, grading says VG+"
    listing["seller_comments_supported"] = True
    result = listing_review(target(), listing, fresh=True)
    assert result["media_defects"]["status"] == "defective"
    assert result["status"] == "blocked"
    assert result["purchase_allowed"] is False
