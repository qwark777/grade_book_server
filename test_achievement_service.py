import os
import unittest


os.environ.setdefault(
    "SECRET_KEY",
    "test_secret_key_123456789012345678901234567890",
)
os.environ.setdefault(
    "ENCRYPTION_KEY",
    "ICkoftk-wbOx89vzo2nuGkPatHZCQ1IKBVpFdRJ1F4k=",
)

from app.services.achievement_service import (  # noqa: E402
    ACHIEVEMENT_CATALOG,
    achievement_catalog_metadata,
    can_manually_award_achievement,
    catalog_codes_for_role,
    competition_rank,
)


class AchievementServiceTests(unittest.TestCase):
    def test_catalog_codes_are_unique(self):
        codes = [item["code"] for item in ACHIEVEMENT_CATALOG]
        self.assertEqual(len(codes), 7)
        self.assertEqual(len(codes), len(set(codes)))

    def test_catalog_is_split_by_role(self):
        self.assertEqual(
            catalog_codes_for_role("student"),
            {
                "class_president",
                "first_place",
                "second_place",
                "third_place",
                "heart_of_class",
            },
        )
        self.assertEqual(
            catalog_codes_for_role("teacher"),
            {"politeness", "olimpiad_coach"},
        )
        self.assertEqual(catalog_codes_for_role("owner"), set())

    def test_competition_rank_does_not_give_lower_medals_to_winner(self):
        rows = [
            {"student_id": 10, "total_points": 50},
            {"student_id": 11, "total_points": 40},
            {"student_id": 12, "total_points": 30},
        ]
        self.assertEqual(competition_rank(rows, 10), 1)
        self.assertEqual(competition_rank(rows, 11), 2)
        self.assertEqual(competition_rank(rows, 12), 3)

    def test_competition_rank_handles_ties(self):
        rows = [
            {"student_id": 10, "total_points": 50},
            {"student_id": 11, "total_points": 50},
            {"student_id": 12, "total_points": 30},
        ]
        self.assertEqual(competition_rank(rows, 10), 1)
        self.assertEqual(competition_rank(rows, 11), 1)
        self.assertEqual(competition_rank(rows, 12), 3)
        self.assertIsNone(competition_rank(rows, 99))

    def test_only_manual_role_appropriate_catalog_items_can_be_awarded(self):
        self.assertTrue(
            can_manually_award_achievement("class_president", "student")
        )
        self.assertTrue(
            can_manually_award_achievement("olimpiad_coach", "teacher")
        )
        self.assertFalse(
            can_manually_award_achievement("first_place", "student")
        )
        self.assertFalse(
            can_manually_award_achievement("class_president", "teacher")
        )

    def test_custom_achievements_remain_manual(self):
        self.assertTrue(
            can_manually_award_achievement("custom_school_award", "student")
        )
        self.assertEqual(
            achievement_catalog_metadata("custom_school_award"),
            {
                "is_manual": True,
                "roles": ["student", "teacher"],
            },
        )


if __name__ == "__main__":
    unittest.main()
