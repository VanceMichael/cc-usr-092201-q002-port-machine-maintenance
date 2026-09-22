import unittest

from pmms.core.errors import ConflictError, WorkflowError
from pmms.domain import vocab as V

from .support import build_world, new_service, open_mechanical_case


class TriageTest(unittest.TestCase):
    def setUp(self):
        self.svc, self.clk = new_service()
        self.c = build_world(self.svc)
        self.svc.raise_alarm(self.c["machine"], self.c["alarm"], {
            "code": "SPREADER_HEIGHT_DEVIATION", "message": "偏差超阈值",
            "source": "PLC"})

    def test_only_supervisor_triages(self):
        from pmms.core.errors import AuthzError
        with self.assertRaises(AuthzError):
            self.svc.triage(self.c["alarm"], "TC-01",
                            V.CAUSE_MECHANICAL, "high", rationale="越权")

    def test_cause_must_match_plan(self):
        self.svc.triage(self.c["alarm"], "SV-01", V.CAUSE_CALIBRATION,
                        "high", rationale="漂移")
        with self.assertRaises(WorkflowError):
            self.svc.open_work_order(self.c["alarm"], "plan-mech", "SV-01")

    def test_cannot_open_work_order_before_triage(self):
        svc, clk = new_service()
        c = build_world(svc)
        svc.raise_alarm(c["machine"], "AL-X",
                        {"code": "X", "message": "m", "source": "p"})
        with self.assertRaises(WorkflowError):
            svc.open_work_order("AL-X", "plan-mech", "SV-01")


class ParallelWorkTest(unittest.TestCase):
    def setUp(self):
        self.svc, self.clk = new_service()
        self.c = build_world(self.svc)

    def test_same_case_single_active_work_order(self):
        aid, _ = open_mechanical_case(self.svc, self.c, self.clk)
        with self.assertRaises(ConflictError):
            self.svc.open_work_order(aid, "plan-mech", "SV-01")

    def test_disjoint_resource_locks_run_in_parallel(self):
        # RMG-03 与 RMG-07 不同资源，并行不互斥
        aid1, _ = open_mechanical_case(self.svc, self.c, self.clk)
        svc = self.svc
        svc.receive_stock("ST-T", "P-TR", "SN-T", "B2026-02")
        svc.publish_plan(
            "plan-tr", "小车编码器", V.CAUSE_MECHANICAL,
            [V.ACTION_REPLACE_PART, V.ACTION_RETEST],
            ["trolley.position_encoder"], "P-TR", 30)
        svc.fit_component("RMG-07", "trolley.position_encoder", "ST-T", "SV-01")
        svc.set_software("RMG-07", "trolley-ctrl", "v1", "p1", "SV-01")
        svc.raise_alarm("RMG-07", "AL-002",
                        {"code": "T", "message": "m", "source": "p"})
        svc.triage("AL-002", "SV-01", V.CAUSE_MECHANICAL, "medium", "m")
        # 不抛冲突
        svc.open_work_order("AL-002", "plan-tr", "SV-01")

    def test_overlapping_slot_lock_conflicts(self):
        aid1, _ = open_mechanical_case(self.svc, self.c, self.clk)
        # 同一设备同一安装位的第二案卷必须等工单结束
        self.svc.raise_alarm(self.c["machine"], "AL-009",
                             {"code": "Z", "message": "m", "source": "p"})
        self.svc.triage("AL-009", "SV-01", V.CAUSE_MECHANICAL, "low", "m")
        with self.assertRaises(ConflictError):
            self.svc.open_work_order("AL-009", "plan-mech", "SV-01")


if __name__ == "__main__":
    unittest.main()
