import unittest

from pmms.core.errors import (
    IntegrityError,
    QualificationError,
    WorkflowError,
)
from pmms.domain import vocab as V

from .support import build_world, new_service, open_mechanical_case


class SignedActionTest(unittest.TestCase):
    def setUp(self):
        self.svc, self.clk = new_service()
        self.c = build_world(self.svc)
        self.aid, self.wo = open_mechanical_case(self.svc, self.c, self.clk)

    def test_replace_part_requires_valid_cert_and_tool(self):
        with self.assertRaises(QualificationError):
            self.svc.replace_part(self.aid, self.c["new_stock"], self.c["slot"],
                                  "TC-EXPIRED", "C-OLD", "TL-TQ")
        with self.assertRaises(QualificationError):
            self.svc.replace_part(self.aid, self.c["new_stock"], self.c["slot"],
                                  "TC-01", "C-TC", "TOOL-NOT-ALLOWED")

    def test_signed_action_records_person_tool_cert_component(self):
        self.svc.replace_part(self.aid, self.c["new_stock"], self.c["slot"],
                              "TC-01", "C-TC", "TL-TQ")
        wo = self.svc.reg.machine_of_alarm(self.aid).alarms[self.aid] \
            .work_orders[0]
        action = next(a for a in wo.actions if a.kind == V.ACTION_REPLACE_PART)
        self.assertEqual(action.by, "TC-01")
        self.assertEqual(action.tool_id, "TL-TQ")
        self.assertEqual(action.detail["serial"], "SN-NEW")
        self.assertEqual(action.detail["removed_serial"], self.c["old_serial"])

    def test_part_must_be_reserved_for_this_work_order(self):
        from pmms.core.errors import InventoryError
        with self.assertRaises(InventoryError):
            self.svc.replace_part(self.aid, "ST-OTHER", self.c["slot"],
                                  "TC-01", "C-TC", "TL-TQ")

    def test_backfill_beyond_window_rejected(self):
        with self.assertRaises(IntegrityError):
            self.svc.replace_part(
                self.aid, self.c["new_stock"], self.c["slot"],
                "TC-01", "C-TC", "TL-TQ",
                occurred_at="2026-09-19T00:00:00Z")  # 超过 72 小时

    def test_backfill_keeps_actual_time(self):
        self.clk.advance(60 * 60)  # 09:04，补录 08:40 实际完成
        ev = self.svc.replace_part(
            self.aid, self.c["new_stock"], self.c["slot"],
            "TC-01", "C-TC", "TL-TQ",
            occurred_at="2026-09-22T08:40:00Z")
        self.assertEqual(ev.occurred_at, "2026-09-22T08:40:00Z")
        self.assertNotEqual(ev.occurred_at, ev.recorded_at)

    def test_off_window_action_rejected(self):
        svc, clk = new_service()
        c = build_world(svc)
        svc.define_window(c["machine"], "win-late",
                          "2026-09-22T20:00:00Z", "2026-09-22T22:00:00Z")
        svc.cancel_window(c["machine"], "win-RMG-03-am")
        svc.raise_alarm(c["machine"], "AL-W",
                        {"code": "X", "message": "m", "source": "p"})
        svc.triage("AL-W", "SV-01", V.CAUSE_MECHANICAL, "high", "m")
        with self.assertRaises(WorkflowError):
            svc.open_work_order("AL-W", "plan-mech", "SV-01")


class RollbackTest(unittest.TestCase):
    def setUp(self):
        self.svc, self.clk = new_service()
        self.c = build_world(self.svc, with_unconfirmed_params=True)
        self.svc.raise_alarm(self.c["machine"], "AL-P",
                             {"code": "PARAM", "message": "m", "source": "p"})
        self.svc.triage("AL-P", "SV-01", V.CAUSE_UNCONFIRMED_PARAMS,
                        "high", "未确认参数")
        self.svc.open_work_order("AL-P", "plan-param", "SV-01")

    def test_rollback_restores_previous_params_and_confirms(self):
        self.svc.rollback_params("AL-P", "spreader-ctrl", "TC-02",
                                 "C-SW", "TL-PRG")
        sw = self.svc.reg.machines[self.c["machine"]].software["spreader-ctrl"]
        self.assertEqual(sw.params_version, "p-1108")
        self.assertTrue(sw.confirmed)

    def test_rollback_requires_software_cert(self):
        from pmms.core.errors import QualificationError
        with self.assertRaises(QualificationError):
            self.svc.rollback_params("AL-P", "spreader-ctrl", "TC-01",
                                     "C-TC", "TL-TQ")


class BypassTest(unittest.TestCase):
    def setUp(self):
        self.svc, self.clk = new_service()
        self.c = build_world(self.svc)
        self.aid, self.wo = open_mechanical_case(self.svc, self.c, self.clk)

    def test_bypass_grants_narrow_capability_then_lifted(self):
        self.svc.grant_bypass(self.aid, V.BYPASS_SCOPE_MANUAL, "SV-01",
                              "C-SUP", "TL-AUTH", reason="移舱", ttl_minutes=20)
        st = self.svc.machine_status(self.c["machine"])
        self.assertIn("remote_manual", st.available_caps)
        self.assertNotIn("auto_hoist", st.available_caps)
        self.svc.lift_bypass(f"bp-{self.aid}-01", "SV-01")
        st = self.svc.machine_status(self.c["machine"])
        self.assertNotIn("remote_manual", st.available_caps)

    def test_expired_unlifted_bypass_blocks(self):
        self.svc.grant_bypass(self.aid, V.BYPASS_SCOPE_MANUAL, "SV-01",
                              "C-SUP", "TL-AUTH", reason="移舱", ttl_minutes=10)
        self.clk.advance(11 * 60)
        st = self.svc.machine_status(self.c["machine"])
        codes = {s.code for s in st.suspensions}
        self.assertIn(V.SUSP_BYPASS_EXPIRED, codes)
        self.assertEqual(st.available_caps, [])

    def test_second_active_bypass_conflicts(self):
        from pmms.core.errors import ConflictError
        self.svc.grant_bypass(self.aid, V.BYPASS_SCOPE_MANUAL, "SV-01",
                              "C-SUP", "TL-AUTH", reason="x", ttl_minutes=20)
        with self.assertRaises(ConflictError):
            self.svc.grant_bypass(self.aid, V.BYPASS_SCOPE_SLOW_TRAVEL,
                                  "SV-01", "C-SUP", "TL-AUTH", reason="y")


if __name__ == "__main__":
    unittest.main()
