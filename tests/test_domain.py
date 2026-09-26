import unittest
from pathlib import Path

from domain_context.loader import load_domain


class DomainFixtureTest(unittest.TestCase):
    def setUp(self):
        self.value = load_domain(Path("fixtures/domain.json"))
        self.sample = self.value["sample"]

    def test_fixture_is_complete(self):
        self.assertEqual(self.value["domain"], "port-machine-maintenance")
        self.assertGreaterEqual(len(self.value["facts"]), 2)

    def test_alarm_raw_text_starts_the_chain(self):
        alarm = self.sample["alarm"]
        self.assertTrue(alarm["raw_text"].startswith("SPREADER HOIST HEIGHT SENSOR SIGNAL JUMP"))
        self.assertEqual(self.sample["diagnostic_session"]["bound_alarm"], alarm["alarm_id"])
        self.assertEqual(set(alarm["candidate_causes"]),
                         {"机械故障", "校准漂移", "未经确认的软件参数"})

    def test_diagnostic_session_is_scoped_and_expiring(self):
        scopes = self.value["diagnostic_scopes"]
        self.assertTrue(scopes["session_required"])
        session = self.sample["diagnostic_session"]
        self.assertTrue(set(session["scopes_granted"]).issubset(set(scopes["allowed_scopes"])))
        self.assertNotIn("人员资质:联系方式", session["scopes_granted"])
        self.assertIn("expires_at", session)

    def test_four_signoff_actions_are_each_recorded(self):
        recorded = {item["action"] for item in self.sample["signoffs"]}
        self.assertEqual(recorded, {"换件", "参数回退", "临时旁路", "复测"})

    def test_offline_entry_keeps_actual_occurrence_time(self):
        replacement = next(s for s in self.sample["signoffs"] if s["action"] == "换件")
        self.assertTrue(replacement["offline_record"])
        self.assertNotEqual(replacement["occurred_at"], replacement["recorded_at"])
        self.assertLess(replacement["occurred_at"], replacement["recorded_at"])
        self.assertTrue(replacement["offline_reason"])
        # 在线签署不得伪造两个不同时间
        rollback = next(s for s in self.sample["signoffs"] if s["action"] == "参数回退")
        self.assertEqual(rollback["occurred_at"], rollback["recorded_at"])

    def test_parallel_work_orders_have_disjoint_lock_scopes(self):
        orders = self.sample["work_orders"]
        scopes = [set(o["lock_scope"]) for o in orders]
        self.assertEqual(len(orders), 2)
        self.assertEqual(scopes[0] & scopes[1], set())

    def test_recall_hit_suspends_capability_immediately(self):
        recall = self.sample["recall"]
        suspended = {e["machine_id"]: e for e in recall["capability_effect"]}
        states = {c["machine_id"]: c for c in self.sample["capability_states"]}
        for machine_id in ("RMG-05", "RMG-07"):
            self.assertIn(machine_id, suspended)
            self.assertEqual(states[machine_id]["state"], "能力暂停")
            self.assertIn("自动着箱", states[machine_id]["suspended_capabilities"])
            self.assertEqual(states[machine_id]["available_capabilities"], [])
        # 已换上非召回批次部件的设备不应被本次召回暂停
        self.assertNotIn("RMG-03", suspended)
        self.assertEqual(states["RMG-03"]["state"], "限制运行")

    def test_dispatch_view_reports_eta_and_available_actions(self):
        view = self.sample["dispatch_view"]
        self.assertEqual(view["eta_resume"], "2026-09-22T10:30:00+08:00")
        self.assertIn("远程人工对位着箱", view["available_actions"])
        self.assertIn("自动着箱", view["suspended_actions"])
        holds = {h["machine_id"]: h for h in view["related_holds"]}
        self.assertIsNone(holds["RMG-05"]["eta_resume"])

    def test_release_answers_who_how_what_version_and_why(self):
        release = self.sample["release"]
        self.assertEqual(release["state"], "已放行")
        self.assertEqual(len(self.value["release_elements"]), 5)
        self.assertIn("param-set-19", release["adopted_version"])
        self.assertTrue(release["resolved_by"])
        self.assertEqual(release["tool_used"], "数显扭矩扳手 WRENCH-C-07")
        self.assertEqual(release["component_resolved"]["serial"], "SN-SP-H-2211-0426")
        self.assertTrue(release["reviewer"])
        self.assertIn("同意恢复", release["review_rationale"])

    def test_bypass_is_closed_before_release_gate(self):
        bypass = next(s for s in self.sample["signoffs"] if s["action"] == "临时旁路")
        release = self.sample["release"]
        self.assertLessEqual(bypass["expires_at"], release["released_at"])
        self.assertTrue(bypass["residual_risk"])
        self.assertTrue(bypass["review_before_expiry_by"])


if __name__ == "__main__":
    unittest.main()
