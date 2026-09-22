"""测试共用的标准世界：两台设备、人员资质、窗口、方案、备件与基线状态。"""

from __future__ import annotations

from datetime import datetime, timezone

from pmms.app import create_service
from pmms.core.time import FixedClock
from pmms.domain import vocab as V

T0 = datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc)


def new_service():
    clk = FixedClock(T0)
    svc = create_service(clock=clk)
    return svc, clk


def build_world(svc, *, with_unconfirmed_params: bool = False):
    """登记标准基线，返回常用标识字典。"""

    svc.register_machine(
        "RMG-03", "自动场桥RMG-03", "RMG-AUTO-41t",
        {"泊位": "QC-A4", "控制器": "spreader-ctrl"})
    svc.register_machine("RMG-07", "自动场桥RMG-07", "RMG-AUTO-41t", {})
    for mid in ("RMG-03", "RMG-07"):
        svc.define_window(mid, f"win-{mid}-am", "2026-09-22T08:00:00Z",
                          "2026-09-22T12:00:00Z", kind="berth_negotiated")

    svc.register_person("RD-01", "远程司机", V.ROLE_REMOTE_DRIVER, [])
    svc.register_person(
        "SV-01", "维护主管", V.ROLE_MAINTENANCE_SUPERVISOR,
        [{"cert_id": "C-SUP", "scope": "supervision",
          "valid_from": "2026-01-01T00:00:00Z", "valid_to": "2026-12-31T23:59:59Z",
          "allowed_tools": ["TL-AUTH"], "actions": [V.ACTION_GRANT_BYPASS]}])
    svc.register_person(
        "TC-01", "现场检修甲", V.ROLE_TECHNICIAN,
        [{"cert_id": "C-TC", "scope": "component:height_sensor",
          "valid_from": "2026-01-01T00:00:00Z", "valid_to": "2026-12-31T23:59:59Z",
          "allowed_tools": ["TL-TQ", "TL-CAL"],
          "actions": [V.ACTION_REPLACE_PART, V.ACTION_CALIBRATE, V.ACTION_RETEST]}])
    svc.register_person(
        "TC-02", "现场检修乙", V.ROLE_TECHNICIAN,
        [{"cert_id": "C-SW", "scope": "software:spreader-ctrl",
          "valid_from": "2026-01-01T00:00:00Z", "valid_to": "2026-12-31T23:59:59Z",
          "allowed_tools": ["TL-PRG"],
          "actions": [V.ACTION_ROLLBACK_PARAMS, V.ACTION_RETEST]}])
    svc.register_person(
        "TC-EXPIRED", "资质过期检修", V.ROLE_TECHNICIAN,
        [{"cert_id": "C-OLD", "scope": "component:height_sensor",
          "valid_from": "2025-01-01T00:00:00Z", "valid_to": "2026-09-01T00:00:00Z",
          "allowed_tools": ["TL-TQ"], "actions": [V.ACTION_REPLACE_PART]}])
    svc.register_person(
        "RV-01", "放行复核", V.ROLE_REVIEWER,
        [{"cert_id": "C-RV", "scope": "release_review",
          "valid_from": "2026-01-01T00:00:00Z", "valid_to": "2026-12-31T23:59:59Z",
          "allowed_tools": [], "actions": []}])
    svc.register_person(
        "RV-02", "参与过检修的复核", V.ROLE_REVIEWER,
        [{"cert_id": "C-RV2", "scope": "release_review",
          "valid_from": "2026-01-01T00:00:00Z", "valid_to": "2026-12-31T23:59:59Z",
          "allowed_tools": ["TL-CAL"],
          "actions": [V.ACTION_RETEST]}])

    svc.publish_plan(
        "plan-mech", "更换传感器", V.CAUSE_MECHANICAL,
        [V.ACTION_REPLACE_PART, V.ACTION_CALIBRATE, V.ACTION_RETEST],
        ["spreader.height_sensor"], "P-HS-770", 45)
    svc.publish_plan(
        "plan-cal", "重新校准", V.CAUSE_CALIBRATION,
        [V.ACTION_CALIBRATE, V.ACTION_RETEST],
        ["spreader.height_sensor"], None, 20)
    svc.publish_plan(
        "plan-param", "参数回退", V.CAUSE_UNCONFIRMED_PARAMS,
        [V.ACTION_ROLLBACK_PARAMS, V.ACTION_RETEST], [], None, 15)

    svc.receive_stock("ST-OLD", "P-HS-770", "SN-OLD", "B2025-08")
    svc.receive_stock("ST-NEW", "P-HS-770", "SN-NEW", "B2026-03")
    svc.receive_stock("ST-OTHER", "P-XX", "SN-XX", "B2026-01")
    svc.fit_component("RMG-03", "spreader.height_sensor", "ST-OLD", "SV-01")
    svc.set_software("RMG-03", "spreader-ctrl", "ctrl-4.8.2", "p-1108",
                     "SV-01", confirmed=True)
    svc.record_calibration(
        "RMG-03", "spreader.height_sensor", "TC-01", "C-TC", "pass",
        {"deviation_mm": 1.0}, "STD-LASER")
    if with_unconfirmed_params:
        svc.deploy_params("RMG-03", "spreader-ctrl", "p-1200", "SV-01",
                          confirmed=False)
    return {
        "machine": "RMG-03", "other_machine": "RMG-07",
        "slot": "spreader.height_sensor", "alarm": "AL-001",
        "new_stock": "ST-NEW", "old_serial": "SN-OLD",
    }


def open_mechanical_case(svc, c, clk, *, alarm_id: str | None = None,
                         reserve_stock: str | None = "__default__"):
    """标准机械故障案卷：报警→分诊→开工→预留新件，返回 alarm_id/wo_id。"""

    aid = alarm_id or c["alarm"]
    svc.raise_alarm(c["machine"], aid, {
        "code": "SPREADER_HEIGHT_DEVIATION",
        "message": "吊具高度偏差超阈值", "source": "PLC-SP-03",
        "severity": "high"})
    clk.advance(60)
    svc.triage(aid, "SV-01", V.CAUSE_MECHANICAL, "high", rationale="机械磨损")
    clk.advance(60)
    svc.open_work_order(aid, "plan-mech", "SV-01")
    stock = c["new_stock"] if reserve_stock == "__default__" else reserve_stock
    if stock:
        svc.reserve_part(aid, stock, "SV-01")
    wo_id = svc.reg.machine_of_alarm(aid).alarms[aid].active_work_order.wo_id
    return aid, wo_id


def complete_repair(svc, aid, wo_id, c, clk, *, stock_id: str | None = None):
    """执行换件→校准→复测通过。"""

    svc.replace_part(aid, stock_id or c["new_stock"], c["slot"],
                     "TC-01", "C-TC", "TL-TQ")
    clk.advance(120)
    svc.record_calibration(
        c["machine"], c["slot"], "TC-01", "C-TC", "pass",
        {"deviation_mm": 1.1}, "STD-LASER",
        alarm_id=aid, wo_id=wo_id, tool_id="TL-CAL")
    clk.advance(120)
    svc.retest(aid, "pass", {"联锁动作": 10}, "STD-LASER",
               "TC-01", "C-TC", "TL-CAL", notes="通过")
