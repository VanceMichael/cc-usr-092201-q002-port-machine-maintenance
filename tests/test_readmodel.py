import unittest

from pmms.domain import vocab as V

from .support import build_world, new_service, open_mechanical_case


class SchedulingViewTest(unittest.TestCase):
    def setUp(self):
        self.svc, self.clk = new_service()
        self.c = build_world(self.svc)

    def test_healthy_machine_offers_all_auto_caps(self):
        st = self.svc.machine_status(self.c["machine"])
        self.assertTrue(st.operational)
        self.assertEqual(
            set(st.available_caps),
            {"auto_hoist", "auto_trolley", "auto_spreader", "auto_travel"})
        self.assertIsNone(st.etr)

    def test_unconfirmed_params_suspend_without_alarm(self):
        self.svc.deploy_params(self.c["machine"], "spreader-ctrl", "p-9999",
                               "SV-01", confirmed=False)
        st = self.svc.machine_status(self.c["machine"])
        self.assertTrue(any(s.code == V.SUSP_UNCONFIRMED
                            for s in st.suspensions))
        self.assertEqual(st.available_caps, [])

    def test_etr_uses_window_when_in_window(self):
        aid, _ = open_mechanical_case(self.svc, self.c, self.clk)
        st = self.svc.machine_status(self.c["machine"])
        # T0 08:00 开工时约 08:04，45 分钟工单 → 约 08:49
        self.assertIsNotNone(st.etr)
        self.assertIn("窗口", st.etr_basis)

    def test_etr_waits_for_next_window_outside_window(self):
        svc, clk = new_service()
        c = build_world(svc)
        svc.cancel_window(c["machine"], "win-RMG-03-am")
        svc.define_window(c["machine"], "win-pm",
                          "2026-09-22T14:00:00Z", "2026-09-22T16:00:00Z")
        svc.raise_alarm(c["machine"], "AL-E",
                        {"code": "E", "message": "m", "source": "p"})
        svc.triage("AL-E", "SV-01", V.CAUSE_MECHANICAL, "high", "m")
        # 无窗口不能开工，调度视图回答：恢复依赖下一窗口起点
        st = svc.machine_status(c["machine"])
        self.assertEqual(st.available_caps, [])
        self.assertEqual(st.etr, "2026-09-22T14:00:00Z")
        self.assertIn("win-pm", st.etr_basis)

    def test_etr_unknown_without_any_window(self):
        svc, clk = new_service()
        c = build_world(svc)
        svc.cancel_window(c["machine"], "win-RMG-03-am")
        svc.raise_alarm(c["machine"], "AL-E",
                        {"code": "E", "message": "m", "source": "p"})
        svc.triage("AL-E", "SV-01", V.CAUSE_MECHANICAL, "high", "m")
        st = svc.machine_status(c["machine"])
        self.assertIsNone(st.etr)
        self.assertIn("窗口", st.etr_basis)

    def test_etr_pending_retest_is_short_review_buffer(self):
        aid, wo = open_mechanical_case(self.svc, self.c, self.clk)
        from tests.support import complete_repair
        complete_repair(self.svc, aid, wo, self.c, self.clk)
        st = self.svc.machine_status(self.c["machine"])
        self.assertIn("等待复核", st.etr_basis)
        self.assertIsNotNone(st.etr)

    def test_fleet_view_lists_every_machine(self):
        rows = self.svc.fleet_status()
        self.assertEqual({r.machine_id for r in rows}, {"RMG-03", "RMG-07"})


if __name__ == "__main__":
    unittest.main()
