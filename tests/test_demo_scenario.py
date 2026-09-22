import unittest

from pmms.demo import build_demo
from pmms.domain.models import CAPABILITY_LABELS


class DemoScenarioTest(unittest.TestCase):
    def test_full_story_releases_and_integrity_holds(self):
        svc = build_demo(data_dir=None, verbose=False)
        report = svc.verify_integrity()
        self.assertEqual(report["status"], "ok")
        self.assertGreater(report["events"], 30)

        case = svc.reg.machine_of_alarm("AL-20260922-018") \
            .alarms["AL-20260922-018"]
        self.assertIsNotNone(case.release)
        # 第一次放行被拒并留痕
        self.assertIsNotNone(case.rejected_reason)
        # 最终在装件为非召回批次
        inst = svc.reg.machines["RMG-03"].components["spreader.height_sensor"]
        self.assertEqual(inst.batch, "B2026-04")
        # 溯源链能回答版本/人/工具/部件/理由
        trace = svc.release_trace("AL-20260922-018")
        self.assertTrue(trace["software"])
        self.assertTrue(trace["actions"])
        self.assertTrue(all(chk["ok"] for chk in trace["gate_checks"].values()))
        self.assertEqual(len(trace["resumed_caps"]), 4)
        # 越权访问曾被拒（远程司机会话无软件参数取用记录）
        rd_sess = next(s for s in case.sessions if s.opened_by == "RD-01")
        self.assertNotIn("software_params", [a.scope for a in rd_sess.accesses])

    def test_final_status_is_released_with_full_capabilities(self):
        svc = build_demo(data_dir=None, verbose=False)
        st = svc.machine_status("RMG-03")
        self.assertTrue(st.operational)
        self.assertEqual(len(st.available_caps), 4)
        labels = {CAPABILITY_LABELS[c] for c in st.available_caps}
        self.assertIn("自动吊具锁止", labels)


if __name__ == "__main__":
    unittest.main()
