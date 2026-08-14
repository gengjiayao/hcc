import os
import tempfile
import unittest

from run import (_homa_profile_from_sizes, derive_homa_profile_from_cdf,
                 derive_homa_profile_from_flow_file)


class HomaPriorityProfileTest(unittest.TestCase):
    def test_priority_classes_are_disjoint_and_byte_weighted(self):
        profile = _homa_profile_from_sizes([100, 100, 1000, 10000], 1000,
                                           data_levels=7)
        self.assertGreaterEqual(profile["unscheduled_levels"], 1)
        self.assertLessEqual(profile["unscheduled_levels"], 6)
        self.assertEqual(profile["scheduled_levels"],
                         7 - profile["unscheduled_levels"])
        self.assertEqual(len(profile["cutoffs"]),
                         profile["unscheduled_levels"] - 1)
        self.assertEqual(profile["cutoffs"], sorted(profile["cutoffs"]))

    def test_empirical_flow_profile_checks_declared_count(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as traffic:
            traffic.write("2\n0 1 3 100 2.0\n1 0 3 200 2.1\n")
            path = traffic.name
        try:
            profile = derive_homa_profile_from_flow_file(path, 100)
            self.assertEqual(profile["unscheduled_levels"], 5)
            with open(path, "w") as traffic:
                traffic.write("3\n0 1 3 100 2.0\n1 0 3 200 2.1\n")
            with self.assertRaises(ValueError):
                derive_homa_profile_from_flow_file(path, 100)
        finally:
            os.unlink(path)

    def test_cdf_sampling_is_deterministic(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as cdf:
            cdf.write("0 0\n100 50\n1000 100\n")
            path = cdf.name
        try:
            first = derive_homa_profile_from_cdf(path, 100, samples=1000)
            second = derive_homa_profile_from_cdf(path, 100, samples=1000)
            self.assertEqual(first, second)
        finally:
            os.unlink(path)

    def test_rejects_empty_workload(self):
        with self.assertRaises(ValueError):
            _homa_profile_from_sizes([], 100)


if __name__ == "__main__":
    unittest.main()
