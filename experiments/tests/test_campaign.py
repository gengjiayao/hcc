import json
from pathlib import Path
import tempfile
import unittest

from experiments.run_campaign import (
    CampaignError,
    expand_campaign,
    parse_flow_count,
    preflight_traffic,
    read_json,
    select_stages,
    traffic_identity,
)
from run import (
    HARD_GUARD_LIFECYCLE_MAX_LINES,
    resolve_guard_components,
    validate_guard_lifecycle_options,
)


REPO = Path(__file__).resolve().parents[2]


class CampaignDefinitionTests(unittest.TestCase):
    def test_minimal_campaign_expands_to_unique_runs(self):
        campaign = read_json(REPO / "experiments/campaigns/reviewer_minimal.json")
        runs = list(expand_campaign(campaign))
        self.assertEqual(len(runs), 165)
        self.assertEqual(len({run["run_key"] for run in runs}), 165)
        params = runs[0]["params"]
        self.assertEqual(params["guard_selective_registration"], 1)
        self.assertEqual(params["guard_proactive_release"], 1)
        self.assertNotIn("guard_oflm", params)

    def test_independent_oflm_switches_and_legacy_precedence(self):
        self.assertEqual(resolve_guard_components(None, 1, 1), (1, 1))
        self.assertEqual(resolve_guard_components(None, 0, 1), (0, 1))
        self.assertEqual(resolve_guard_components(None, 1, 0), (1, 0))
        self.assertEqual(resolve_guard_components(None, 0, 0), (0, 0))
        self.assertEqual(resolve_guard_components(0, 1, 1), (0, 0))
        self.assertEqual(resolve_guard_components(1, 0, 1), (0, 1))

    def test_lifecycle_trace_is_bounded_and_guard_only(self):
        validate_guard_lifecycle_options(0, "hpcc", None, 1)
        validate_guard_lifecycle_options(
            1, "guard", "/tmp/lifecycle.csv",
            HARD_GUARD_LIFECYCLE_MAX_LINES)
        with self.assertRaisesRegex(ValueError, "must be in"):
            validate_guard_lifecycle_options(1, "guard", None, 0)
        with self.assertRaisesRegex(ValueError, "must be in"):
            validate_guard_lifecycle_options(
                1, "guard", None, HARD_GUARD_LIFECYCLE_MAX_LINES + 1)
        with self.assertRaisesRegex(ValueError, "requires a GUARD mode"):
            validate_guard_lifecycle_options(1, "hpcc", None, 16)
        with self.assertRaisesRegex(ValueError, "requires --guard_lifecycle_trace"):
            validate_guard_lifecycle_options(0, "guard", "/tmp/lifecycle.csv", 16)

    def test_traffic_identity_accounts_for_oversubscription(self):
        params = {
            "topo": "leaf_spine_16_100G_OS4", "cdf": "AliStorage2019",
            "netload": 40, "simul_time": 0.02, "bw": 100, "seed": 3,
        }
        identity = traffic_identity(REPO, params)
        self.assertEqual(identity["hosts"], 16)
        self.assertEqual(identity["hostload_percent"], 10)
        self.assertIn("L_10.00_CDF_AliStorage2019_N_16_T_20ms_B_100_S_3", str(identity["path"]))

    def test_stage_selection_is_explicit_and_validated(self):
        runs = [{"stage": "one"}, {"stage": "two"}, {"stage": "one"}]
        self.assertEqual(len(select_stages(runs, ["one"])), 2)
        with self.assertRaisesRegex(CampaignError, "unknown campaign stage"):
            select_stages(runs, ["missing"])

    def test_flow_count_detects_header_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flow.txt"
            path.write_text("2\n0 1 3 100 2.0\n", encoding="utf-8")
            with self.assertRaises(CampaignError):
                parse_flow_count(path)

    def test_preflight_rejects_before_installing_oversized_traffic(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "config").mkdir()
            (repo / "traffic_gen").mkdir()
            (repo / "config/tiny_OS1.txt").write_text("3 1 0\n", encoding="utf-8")
            (repo / "traffic_gen/Test.txt").write_text("0 0\n1 100\n", encoding="utf-8")
            (repo / "traffic_gen/traffic_gen.py").write_text(
                "import sys\nfrom pathlib import Path\n"
                "p=Path(sys.argv[sys.argv.index('-o')+1])\n"
                "p.write_text('2\\n0 1 3 100 2.0\\n1 0 3 100 2.1\\n')\n",
                encoding="utf-8",
            )
            params = {"topo": "tiny_OS1", "cdf": "Test", "netload": 10,
                      "simul_time": 0.01, "bw": 100, "seed": 1}
            with self.assertRaisesRegex(CampaignError, "flow preflight rejected"):
                preflight_traffic(repo, params, flow_limit=1, install=True)
            self.assertFalse(Path(traffic_identity(repo, params)["path"]).exists())


if __name__ == "__main__":
    unittest.main()
