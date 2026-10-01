import unittest
from datetime import UTC, datetime

from finder.categories.vinyl_covers import (
    conflicting_catalog_cover,
    cover_name,
)
from finder.domain import Listing, Variant

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def variant(name="Alpha", id="1", **kw):
    return Variant(
        catalog_source="synthetic",
        catalog_variant_id=id,
        catalog_product_id="family",
        title="Example Album",
        artists=["Example Artist"],
        formats=[
            {"name": "Vinyl", "descriptions": ["LP"]},
            {"name": "All Media", "text": f"{name} Alternative Cover"},
        ],
        observed_at=NOW,
        **kw,
    )


def listing(suffix, specifics=None):
    return Listing(
        marketplace="synthetic",
        marketplace_item_id="synthetic",
        title="Example Artist Example Album " + suffix,
        item_specifics=specifics or {},
        first_observed_at=NOW,
        last_observed_at=NOW,
    )


class CoverTests(unittest.TestCase):
    def check(self, suffix, expected=None, specifics=None):
        self.assertEqual(
            conflicting_catalog_cover(
                listing(suffix, specifics), variant(), [variant("Beta", "2")]
            ),
            expected,
        )

    def test_explicit(self):
        for value in [
            "Beta cover vinyl",
            "vinyl Beta alternative artwork",
            "LP edition Beta",
            "Beta limited edition 2LP",
        ]:
            with self.subTest(value=value):
                self.check(value, "beta")

    def test_explicit_absence_comparison_and_song_reference(self):
        for value in [
            "Beta cover excluded vinyl",
            "vinyl unlike Beta cover",
            "Beta cover missing LP",
            "Beta cover song vinyl",
            "Beta cover shown for reference LP",
        ]:
            with self.subTest(value=value):
                self.check(value)

    def test_catalog_named_alias_with_record_context(self):
        self.check("Beta vinyl LP", "beta")

    def test_unknown_name(self):
        self.check("Gamma vinyl LP")

    def test_credit_and_related_product_mentions(self):
        for value in [
            "vinyl feat Beta",
            "vinyl produced by Beta",
            "vinyl alongside Beta",
            "vinyl with Beta",
            "vinyl Beta bonus",
        ]:
            with self.subTest(value=value):
                self.check(value)

    def test_financial_and_group_buy_context(self):
        for suffix in [
            "Group purchase vinyl",
            "vinyl Group buy",
            "vinyl Cash App accepted",
            "Cash App payment vinyl",
        ]:
            name = "Group" if "Group" in suffix else "Cash App"
            with self.subTest(suffix=suffix):
                self.assertIsNone(
                    conflicting_catalog_cover(listing(suffix), variant(), [variant(name, "2")])
                )

    def test_target(self):
        self.check("Alpha cover LP")

    def test_negation_choices_bundles(self):
        for value in [
            "not Beta cover LP",
            "Beta cover or Alpha cover LP",
            "Beta cover + vinyl LP",
            "Beta cover with signed insert LP",
            "Beta cover and insert LP",
            "Beta cover? LP",
            "Beta cover bundle LP",
            "Beta cover only",
            "Beta cover vinyl set",
        ]:
            with self.subTest(value=value):
                self.check(value)

    def test_accessory(self):
        for value in [
            "Beta cover insert vinyl LP",
            "Beta cover poster LP",
            "Beta cover replacement LP",
            "Beta cover sleeve LP",
        ]:
            with self.subTest(value=value):
                self.check(value)

    def test_both_target_and_other(self):
        self.check("Alpha Beta cover LP")

    def test_typo_target_abstains(self):
        self.check("Alphx Beta cover LP")

    def test_target_other_field_abstains(self):
        self.check("Beta cover LP", specifics={"Features": ["Alpha cover"]})

    def test_no_record_claim(self):
        self.check("Beta cover")

    def test_same_cover_does_not_conflict(self):
        self.assertIsNone(
            conflicting_catalog_cover(listing("Alpha cover LP"), variant(), [variant("Alpha", "2")])
        )

    def test_unrelated_sibling(self):
        sibling = variant("Beta", "2").model_copy(update={"catalog_product_id": "another"})
        self.assertIsNone(conflicting_catalog_cover(listing("Beta cover LP"), variant(), [sibling]))

    def test_other_artist(self):
        sibling = variant("Beta", "2").model_copy(update={"artists": ["Other Artist"]})
        self.assertIsNone(conflicting_catalog_cover(listing("Beta cover LP"), variant(), [sibling]))

    def test_unknown_target(self):
        target = variant().model_copy(update={"formats": [{"name": "Vinyl", "text": "Blue"}]})
        self.assertIsNone(
            conflicting_catalog_cover(listing("Beta cover LP"), target, [variant("Beta", "2")])
        )

    def test_catalog_markers(self):
        for text in [
            "Beta Alternative Artwork",
            "Signed Insert, Beta Alternative Cover",
            "Beta, Alternative Artwork",
        ]:
            with self.subTest(text=text):
                self.assertEqual(
                    cover_name(
                        variant().model_copy(update={"formats": [{"name": "Vinyl", "text": text}]})
                    ),
                    "beta",
                )

    def test_catalog_unknowns(self):
        for text in [
            "Alternative Artwork",
            "Signed Insert, Alternative Cover",
            "Blue Alternative Cover",
            "Alpha or Beta Alternative Cover",
            "Alpha + Beta Alternative Cover",
            "Beta cover",
            "Limited Edition, Alternative Artwork",
        ]:
            with self.subTest(text=text):
                self.assertIsNone(
                    cover_name(
                        variant().model_copy(update={"formats": [{"name": "Vinyl", "text": text}]})
                    )
                )

    def test_album_name_not_cover_claim(self):
        target = variant().model_copy(update={"title": "Beta Cover"})
        row = listing("LP").model_copy(update={"title": "Example Artist Beta Cover LP"})
        sibling = variant("Beta", "2").model_copy(update={"title": "Beta Cover"})
        self.assertIsNone(conflicting_catalog_cover(row, target, [sibling]))

    def test_target_barcode_abstains(self):
        target = variant(identifiers={"Barcode": ["00012345678905"]})
        self.assertIsNone(
            conflicting_catalog_cover(
                listing("Beta cover LP", {"Barcode": ["00012345678905"]}),
                target,
                [variant("Beta", "2")],
            )
        )

    def test_inexact_identifier_is_not_promoted(self):
        self.check("Beta cover LP", "beta", {"Barcode": ["00012345678912"]})


if __name__ == "__main__":
    unittest.main()
