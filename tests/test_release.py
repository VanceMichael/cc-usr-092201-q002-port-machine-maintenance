import unittest

from pmms.core.errors import QualificationError, ReleaseGateError
from pmms.domain import vocab as V

from .support import build_world, complete_repair, new_service, open_mechanical_case


class ReleaseGateTest(unittest.TestCase):
    def setUp(self):
        self.svc, self.clk = new_service()
        self.c = build_world(self.svc)
        self.aid, self.wo = open_mechanical_case(self.svc, self.c, self.clk)

    def _full_repair(self):
        complete_repair(self.svc, self.aid, self.wo, self.c, self.clk)

    def test_release_rejected_before_retest(self):
        self.svc.replace_part(self.aid, self.c["new_stock"], self.c["slot"],
                              "TC-01", "C-TC", "TL-TQ")
        self.clk.advance(60)
        self.svc.record_calibration(
            self.c["machine"], self.c["slot"], "TC-01", "C-TC", "pass",
            {"deviation_mm": 1.0}, "STD-LASER",
            alarm_id=self.aid, wo_id=self.wo, tool_id="TL-CAL")
        with self.assertRaises(ReleaseGateError):
            self.svc.request_release(self.aid, "RV-01", "C-RV", "未复测")
        case = self.svc.reg.machine_of_alarm(self.aid).alarms[self.aid]
        self.assertEqual(case.state, V.ALARM_REJECTED)
        self.assertIn("retest_pass", case.rejected_reason)

    def test_failed_retest_blocks_release(self):
        self.svc.replace_part(self.aid, self.c["new_stock"], self.c["slot"],
                              "TC-01", "C-TC", "TL-TQ")
        self.clk.advance(60)
        self.svc.record_calibration(
            self.c["machine"], self.c["slot"], "TC-01", "C-TC", "pass",
            {"deviation_mm": 1.0}, "STD-LASER",
            alarm_id=self.aid, wo_id=self.wo, tool_id="TL-CAL")
        self.clk.advance(60)
        self.svc.retest(self.aid, "fail", {}, "STD-LASER",
                        "TC-01", "C-TC", "TL-CAL", notes="偏差仍超差")
        with self.assertRaises(ReleaseGateError):
            self.svc.request_release(self.aid, "RV-01", "C-RV", "复测失败")

    def test_reviewer_who_signed_action_cannot_release(self):
        # RV-02 亲自执行复测并签署 → 不得作为本案卷放行人
        self.svc.replace_part(self.aid, self.c["new_stock"], self.c["slot"],
                              "TC-01", "C-TC", "TL-TQ")
        self.clk.advance(60)
        self.svc.record_calibration(
            self.c["machine"], self.c["slot"], "TC-01", "C-TC", "pass",
            {"deviation_mm": 1.0}, "STD-LASER",
            alarm_id=self.aid, wo_id=self.wo, tool_id="TL-CAL")
        self.clk.advance(60)
        self.svc.retest(self.aid, "pass", {}, "STD-LASER",
                        "RV-02", "C-RV2", "TL-CAL", notes="复核亲自复测")
        with self.assertRaises(ReleaseGateError) as ctx:
            self.svc.request_release(self.aid, "RV-02", "C-RV2", "自己放行")
        self.assertIn("reviewer_independent", str(ctx.exception))

    def test_only_reviewer_role_can_release(self):
        self._full_repair()
        from pmms.core.errors import AuthzError
        with self.assertRaises(AuthzError):
            self.svc.request_release(self.aid, "SV-01", "C-SUP", "主管放行")

    def test_expired_review_cert_rejected(self):
        self._full_repair()
        self.svc.register_person(
            "RV-OLD", "过期复核", V.ROLE_REVIEWER,
            [{"cert_id": "C-RV-OLD", "scope": "release_review",
              "valid_from": "2025-01-01T00:00:00Z",
              "valid_to": "2026-09-01T00:00:00Z",
              "allowed_tools": [], "actions": []}])
        with self.assertRaises(QualificationError):
            self.svc.request_release(self.aid, "RV-OLD", "C-RV-OLD", "过期")

    def test_bypass_must_be_lifted_before_release(self):
        self.svc.grant_bypass(self.aid, V.BYPASS_SCOPE_MANUAL, "SV-01",
                              "C-SUP", "TL-AUTH", reason="移舱", ttl_minutes=60)
        self.svc.replace_part(self.aid, self.c["new_stock"], self.c["slot"],
                              "TC-01", "C-TC", "TL-TQ")
        self.clk.advance(60)
        self.svc.record_calibration(
            self.c["machine"], self.c["slot"], "TC-01", "C-TC", "pass",
            {"deviation_mm": 1.0}, "STD-LASER",
            alarm_id=self.aid, wo_id=self.wo, tool_id="TL-CAL")
        self.clk.advance(60)
        self.svc.retest(self.aid, "pass", {}, "STD-LASER",
                        "TC-01", "C-TC", "TL-CAL")
        with self.assertRaises(ReleaseGateError):
            self.svc.request_release(self.aid, "RV-01", "C-RV", "旁路未解除")

    def test_unconfirmed_params_block_release(self):
        self._full_repair()
        self.svc.deploy_params(self.c["machine"], "spreader-ctrl", "p-1300",
                               "SV-01", confirmed=False)
        with self.assertRaises(ReleaseGateError) as ctx:
            self.svc.request_release(self.aid, "RV-01", "C-RV", "参数未确认")
        self.assertIn("params_confirmed", str(ctx.exception))

    def test_happy_path_trace_answers_version_person_tool_component(self):
        self._full_repair()
        result = self.svc.request_release(
            self.aid, "RV-01", "C-RV", "全部条件满足，同意恢复自动能力")
        self.assertEqual(set(result["resumed_caps"]),
                         {"auto_hoist", "auto_trolley", "auto_spreader",
                          "auto_travel"})
        trace = self.svc.release_trace(self.aid)
        kinds = {a["kind"] for a in trace["actions"]}
        self.assertEqual(
            kinds, {V.ACTION_REPLACE_PART, V.ACTION_CALIBRATE, V.ACTION_RETEST})
        # 版本
        self.assertEqual(trace["software"][0]["version"], "ctrl-4.8.2")
        self.assertEqual(trace["software"][0]["params_version"], "p-1108")
        # 谁用什么工具处理了哪个部件
        replace = next(a for a in trace["actions"]
                       if a["kind"] == V.ACTION_REPLACE_PART)
        self.assertEqual(replace["by"], "TC-01")
        self.assertEqual(replace["tool_id"], "TL-TQ")
        self.assertEqual(replace["detail"]["slot"], self.c["slot"])
        self.assertEqual(replace["detail"]["serial"], "SN-NEW")
        # 复核为何同意
        self.assertEqual(trace["review_rationale"], "全部条件满足，同意恢复自动能力")
        # 闸门证据全部可核
        self.assertTrue(all(c["ok"] for c in trace["gate_checks"].values()))

    def test_calibration_drift_path_releases_without_part_change(self):
        svc, clk = new_service()
        c = build_world(svc)
        svc.raise_alarm(c["machine"], "AL-C",
                        {"code": "DRIFT", "message": "m", "source": "p"})
        svc.triage("AL-C", "SV-01", V.CAUSE_CALIBRATION, "high", "漂移")
        svc.open_work_order("AL-C", "plan-cal", "SV-01")
        case = svc.reg.machine_of_alarm("AL-C").alarms["AL-C"]
        wo = case.active_work_order.wo_id
        clk.advance(300)
        svc.record_calibration(
            c["machine"], c["slot"], "TC-01", "C-TC", "pass",
            {"deviation_mm": 0.8}, "STD-LASER",
            alarm_id="AL-C", wo_id=wo, tool_id="TL-CAL")
        clk.advance(300)
        svc.retest("AL-C", "pass", {}, "STD-LASER", "TC-01", "C-TC", "TL-CAL")
        result = svc.request_release("AL-C", "RV-01", "C-RV", "校准恢复")
        self.assertIn("auto_hoist", result["resumed_caps"])


if __name__ == "__main__":
    unittest.main()
