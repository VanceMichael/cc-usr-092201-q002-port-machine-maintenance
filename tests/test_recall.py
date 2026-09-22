import unittest

from pmms.core.errors import InventoryError
from pmms.domain import vocab as V

from .support import build_world, complete_repair, new_service, open_mechanical_case


class RecallTest(unittest.TestCase):
    def setUp(self):
        self.svc, self.clk = new_service()
        self.c = build_world(self.svc)

    def test_recall_on_installed_component_suspends_capability(self):
        svc = self.svc
        # 在装件为批次 B2025-08 的 SN-OLD
        svc.issue_recall("RC-1", "P-HS-770", ["B2025-08"], "零漂风险", by="SV-01")
        st = svc.machine_status(self.c["machine"])
        self.assertTrue(any(s.code == V.SUSP_RECALL for s in st.suspensions))
        self.assertEqual(st.available_caps, [])
        self.assertIsNone(st.etr)
        self.assertIn("召回", st.etr_basis)

    def test_recall_blocks_reservation_and_fitting(self):
        svc = self.svc
        svc.issue_recall("RC-2", "P-HS-770", ["B2026-03"], "零漂", by="SV-01")
        with self.assertRaises(InventoryError):
            svc.fit_component(self.c["other_machine"], "x", self.c["new_stock"],
                              "SV-01")
        # 案卷工单只能预留未受影响批次；受召回备件在预留环节即被拦截
        svc.receive_stock("ST-SAFE", "P-HS-770", "SN-SAFE", "B2026-04")
        aid, wo = open_mechanical_case(svc, self.c, self.clk, reserve_stock=None)
        with self.assertRaises(InventoryError):
            svc.reserve_part(aid, self.c["new_stock"], "SV-01")
        svc.reserve_part(aid, "ST-SAFE", "SV-01")  # 安全批次可预留

    def test_mid_repair_recall_blocks_release_until_part_swapped(self):
        svc = self.svc
        aid, wo = open_mechanical_case(svc, self.c, self.clk)
        complete_repair(svc, aid, wo, self.c, self.clk)
        # 复测已通过；此时命中召回
        svc.issue_recall("RC-3", "P-HS-770", ["B2026-03"], "零漂", by="SV-01")
        from pmms.core.errors import ReleaseGateError
        with self.assertRaises(ReleaseGateError):
            svc.request_release(aid, "RV-01", "C-RV", "复核")
        # 换新批次重新处置后可以放行
        svc.receive_stock("ST-SAFE", "P-HS-770", "SN-SAFE", "B2026-04")
        case = svc.reg.machine_of_alarm(aid).alarms[aid]
        first_wo = case.active_work_order.wo_id
        svc.cancel_work_order(aid, first_wo, "SV-01", "命中召回")
        svc.open_work_order(aid, "plan-mech", "SV-01")
        svc.reserve_part(aid, "ST-SAFE", "SV-01")
        wo2 = svc.reg.machine_of_alarm(aid).alarms[aid] \
            .active_work_order.wo_id
        complete_repair(svc, aid, wo2, self.c, self.clk, stock_id="ST-SAFE")
        result = svc.request_release(aid, "RV-01", "C-RV", "新批次复核通过")
        self.assertIn("auto_hoist", result["resumed_caps"])
        # 命中召回的旧件仍在部件履历中且已拆出
        m = svc.reg.machines[self.c["machine"]]
        self.assertEqual(m.components[self.c["slot"]].batch, "B2026-04")


if __name__ == "__main__":
    unittest.main()
