import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HW_CC = (ROOT / "src/point-to-point/model/rdma-hw.cc").read_text(
    encoding="utf-8")
HW_H = (ROOT / "src/point-to-point/model/rdma-hw.h").read_text(
    encoding="utf-8")
RUNNER = (ROOT / "run.py").read_text(encoding="utf-8")
SCRATCH = (ROOT / "scratch/network-load-balance.cc").read_text(
    encoding="utf-8")


class GuardSizeClassConcurrencyV33Test(unittest.TestCase):
    def test_dyadic_rule_is_overflow_safe_and_remaining_ordered(self):
        self.assertIn(
            "second_remaining - shortest_remaining <= shortest_remaining ? 2 : 1",
            HW_CC,
        )
        sort_at = HW_CC.index("std::sort(elephants.begin(), elephants.end()")
        choose_at = HW_CC.index(
            "ComputeGuardSizeClassElephantConcurrency(", sort_at)
        self.assertLess(sort_at, choose_at)
        self.assertIn("remainingBytes < right.remainingBytes", HW_CC)

    def test_feature_is_default_off_and_fail_closed(self):
        self.assertIn("bool m_guardSizeClassElephantConcurrency;", HW_H)
        self.assertIn("BooleanValue(false)", HW_CC)
        self.assertIn(
            "--guard_size_class_elephant_concurrency", RUNNER)
        self.assertIn(
            "size-class elephant concurrency requires receiver concurrency 2",
            RUNNER,
        )
        self.assertIn(
            "adaptive and size-class elephant concurrency are exclusive",
            RUNNER,
        )
        self.assertIn(
            "GUARD_SIZE_CLASS_ELEPHANT_CONCURRENCY", SCRATCH)
        self.assertIn("guard_receiver_concurrency != 2", SCRATCH)
        self.assertIn("guard_size_class_elephant_concurrency ||", SCRATCH)

    def test_promotions_have_dedicated_observable_counters(self):
        for token in (
            "m_guardSizeClassConcurrencyPromotions",
            "m_guardSizeClassConcurrencyMaxEffective",
            "size_class_promotions",
            "size_class_max_effective",
        ):
            self.assertIn(token, HW_H + HW_CC + SCRATCH)


if __name__ == "__main__":
    unittest.main()
