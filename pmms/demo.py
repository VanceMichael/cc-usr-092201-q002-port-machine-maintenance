"""端到端业务故事：自动场桥 RMG-03 吊具高度传感器异常的处置全流程。

run_demo() 既可写入 data/ 目录供 CLI 独立运行，也可在内存中供测试复用。
时间用固定时钟推进，所有时间线均可复现。
"""

from __future__ import annotations

from datetime import datetime, timezone

from .app import create_service
from .core.errors import AuthzError, ConflictError, ReleaseGateError
from .core.time import FixedClock
from .domain import vocab as V
from .domain.models import CAPABILITY_LABELS

START = datetime(2026, 9, 22, 8, 50, tzinfo=timezone.utc)


def _line(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def _status(svc, machine_id: str = "RMG-03") -> None:
    st = svc.machine_status(machine_id)
    print(f"[{st.state}] {st.name}")
    if st.suspensions:
        for s in st.suspensions:
            print(f"  挂起 · {V.SUSPENSION_LABELS.get(s.code, s.code)}：{s.detail}")
    else:
        print("  挂起 · 无")
    caps = "、".join(CAPABILITY_LABELS.get(c, c) for c in st.available_caps) or "无"
    print(f"  可承担动作 · {caps}")
    print(f"  预计恢复 · {st.etr or '待定'}（{st.etr_basis}）")


def build_demo(data_dir=None, clock=None, verbose=False):
    clk = clock or FixedClock(START)
    svc = create_service(data_dir, clk)

    def p(msg):
        if verbose:
            print(msg)

    def tick(minutes: int):
        clk.advance(minutes * 60)

    # ---------------- 基线资料 ----------------

    if verbose:
        _line("08:50 基线：设备、人员资质、停机窗口、方案、备件、在装部件")

    svc.register_machine(
        "RMG-03", "自动场桥RMG-03", "RMG-AUTO-41t",
        config={"泊位": "QC-A4", "额定载荷_t": 41, "吊具型号": "SP-20FT",
                "远程操控室": "ROC-1", "控制器": "spreader-ctrl"},
    )
    svc.register_machine(
        "RMG-07", "自动场桥RMG-07", "RMG-AUTO-41t",
        config={"泊位": "QC-B2", "额定载荷_t": 41, "吊具型号": "SP-20FT"},
    )
    for mid in ("RMG-03", "RMG-07"):
        svc.define_window(mid, f"win-{mid}-am", "2026-09-22T08:30:00Z",
                          "2026-09-22T12:00:00Z", kind="berth_negotiated",
                          note="靠泊船期协调窗口")

    svc.register_person("RD-01", "远程司机·林海", V.ROLE_REMOTE_DRIVER, [],
                        secret="driver-secret")
    svc.register_person(
        "SV-01", "维护主管·周岭", V.ROLE_MAINTENANCE_SUPERVISOR,
        [{"cert_id": "C-SUP-01", "scope": "supervision",
          "valid_from": "2026-01-01T00:00:00Z", "valid_to": "2026-12-31T23:59:59Z",
          "allowed_tools": ["TL-AUTH-02"],
          "actions": [V.ACTION_GRANT_BYPASS]}],
        secret="sup-secret")
    svc.register_person(
        "TC-01", "现场检修·韩铎", V.ROLE_TECHNICIAN,
        [{"cert_id": "C-TC-01", "scope": "component:height_sensor",
          "valid_from": "2025-06-01T00:00:00Z", "valid_to": "2027-05-31T23:59:59Z",
          "allowed_tools": ["TL-TQ-07", "TL-CAL-09"],
          "actions": [V.ACTION_REPLACE_PART, V.ACTION_CALIBRATE, V.ACTION_RETEST]}],
        secret="tech-secret")
    svc.register_person(
        "RV-01", "放行复核·闻璟", V.ROLE_REVIEWER,
        [{"cert_id": "C-RV-01", "scope": "release_review",
          "valid_from": "2026-01-01T00:00:00Z", "valid_to": "2026-12-31T23:59:59Z",
          "allowed_tools": [], "actions": []}],
        secret="rev-secret")

    svc.publish_plan(
        "plan-mech", "更换吊具高度传感器并复测", V.CAUSE_MECHANICAL,
        required_actions=[V.ACTION_REPLACE_PART, V.ACTION_CALIBRATE, V.ACTION_RETEST],
        required_slots=["spreader.height_sensor"], required_part_no="P-HS-770",
        estimated_minutes=45)
    svc.publish_plan(
        "plan-cal", "吊具高度传感器重新校准", V.CAUSE_CALIBRATION,
        required_actions=[V.ACTION_CALIBRATE, V.ACTION_RETEST],
        required_slots=["spreader.height_sensor"], required_part_no=None,
        estimated_minutes=20)
    svc.publish_plan(
        "plan-param", "控制参数回退并复测", V.CAUSE_UNCONFIRMED_PARAMS,
        required_actions=[V.ACTION_ROLLBACK_PARAMS, V.ACTION_RETEST],
        required_slots=[], required_part_no=None, estimated_minutes=15)

    svc.receive_stock("ST-OLD-1", "P-HS-770", "SN-OLD-7741", "B2025-08")
    svc.receive_stock("ST-NEW-1", "P-HS-770", "SN-NEW-9002", "B2026-03")
    svc.fit_component("RMG-03", "spreader.height_sensor", "ST-OLD-1", "SV-01")
    svc.set_software("RMG-03", "spreader-ctrl", "ctrl-4.8.2", "p-1108", "SV-01",
                     confirmed=True)
    svc.record_calibration(
        "RMG-03", "spreader.height_sensor", "TC-01", "C-TC-01",
        "pass", {"height_mm": [0, 500, 12000]}, "STD-LASER-03")
    # 9 月 15 日的校准已出现漂移——这是诊断的关键证据之一
    svc.record_calibration(
        "RMG-03", "spreader.height_sensor", "TC-01", "C-TC-01",
        "drift", {"deviation_mm": 22}, "STD-LASER-03",
        occurred_at="2026-09-15T02:10:00Z", historical=True)
    p("已登记 2 台设备、4 名人员、3 套检修方案；RMG-03 在装 SN-OLD-7741（B2025-08）")

    if verbose:
        _status(svc)

    # ---------------- 1. 报警原文接入 ----------------

    tick(5)
    if verbose:
        _line("08:55 报警原文接入：联锁触发，远程司机被自动接管中断")
    svc.raise_alarm(
        "RMG-03", "AL-20260922-018",
        {"code": "SPREADER_HEIGHT_DEVIATION",
         "message": "吊具高度反馈偏差 38mm 超过阈值 25mm，自动吊具联锁触发",
         "source": "PLC-SP-03", "severity": "high",
         "threshold_mm": 25, "observed_mm": 38})
    p("报警 AL-20260922-018 原文已入链")
    if verbose:
        _status(svc)

    # ---------------- 2. 授权诊断会话内取证 ----------------

    tick(2)
    if verbose:
        _line("08:57 维护主管在授权会话内取证（最小必要数据，逐次留痕）")
    _, token_sup = svc.open_diag_session(
        "AL-20260922-018", "SV-01",
        purpose="区分机械故障/校准漂移/未确认软件参数，决定检修路径",
        data_scopes=[V.DATA_ALARM_RAW, V.DATA_MACHINE_CONFIG,
                     V.DATA_COMPONENT_HISTORY, V.DATA_SOFTWARE_PARAMS,
                     V.DATA_CALIBRATION], ttl_minutes=30)
    for scope, why in [
        (V.DATA_ALARM_RAW, "核对原始偏差与阈值"),
        (V.DATA_SOFTWARE_PARAMS, "排除昨夜参数推送且未确认"),
        (V.DATA_CALIBRATION, "查看近期校准漂移趋势"),
        (V.DATA_COMPONENT_HISTORY, "查在装传感器序列号与批次"),
    ]:
        svc.access_diagnostic_data(token_sup, scope, why)
    p("主管取证：参数仍为已确认的 ctrl-4.8.2/p-1108；校准 9/15 已漂移 22mm；"
      "在装件 SN-OLD-7741 已服役 14 个月")

    if verbose:
        print("\n远程司机申请仅含两类数据的会话，越权读取软件参数应被拒绝：")
    _, token_rd = svc.open_diag_session(
        "AL-20260922-018", "RD-01",
        purpose="远程司机确认联锁范围以配合移舱",
        data_scopes=[V.DATA_ALARM_RAW, V.DATA_MACHINE_CONFIG], ttl_minutes=15)
    try:
        svc.access_diagnostic_data(token_rd, V.DATA_SOFTWARE_PARAMS, "越权尝试")
    except AuthzError as exc:
        p(f"已拒绝越权访问：{exc}")
    rd_session = next(s for s in svc.reg.machine_of_alarm("AL-20260922-018")
                      .alarms["AL-20260922-018"].sessions if s.opened_by == "RD-01")
    svc.close_diag_session(rd_session.session_id, "RD-01")

    # ---------------- 3. 分诊 ----------------

    tick(1)
    if verbose:
        _line("09:00 分诊：维护主管判定为机械故障")
    svc.triage("AL-20260922-018", "SV-01", V.CAUSE_MECHANICAL, "high",
               rationale="参数集 p-1108 已确认未变更；校准漂移已持续且偏差扩大，"
                         "结合传感器服役年限，判定为传感器本体机械故障")
    svc.close_diag_session(
        next(s for s in svc.reg.machine_of_alarm("AL-20260922-018")
             .alarms["AL-20260922-018"].sessions if s.opened_by == "SV-01").session_id,
        "SV-01")
    p("结论：机械故障（置信度 high），适用方案 plan-mech")

    # ---------------- 4. 开工单与并行控制 ----------------

    tick(2)
    if verbose:
        _line("09:02 停机窗口内开工单；并行检修不得互相覆盖")
    svc.open_work_order("AL-20260922-018", "plan-mech", "SV-01")
    svc.reserve_part("AL-20260922-018", "ST-NEW-1", "SV-01")
    p("工单 wo-AL-20260922-018-01 开启，锁定 machine:RMG-03 与 "
      "slot:RMG-03:spreader.height_sensor；预留 SN-NEW-9002")
    try:
        svc.open_work_order("AL-20260922-018", "plan-mech", "SV-01")
    except ConflictError as exc:
        p(f"同案卷重复开工被拒：{exc}")

    # 另一台设备的并行作业：资源不交集，可以同时进行
    svc.receive_stock("ST-TROLLEY-1", "P-TR-220", "SN-TR-5510", "B2026-02")
    svc.publish_plan(
        "plan-trolley", "更换小车位置编码器", V.CAUSE_MECHANICAL,
        required_actions=[V.ACTION_REPLACE_PART, V.ACTION_RETEST],
        required_slots=["trolley.position_encoder"], required_part_no="P-TR-220",
        estimated_minutes=30)
    svc.fit_component("RMG-07", "trolley.position_encoder", "ST-TROLLEY-1", "SV-01")
    svc.set_software("RMG-07", "trolley-ctrl", "ctrl-3.5.0", "p-0902", "SV-01")
    svc.raise_alarm(
        "RMG-07", "AL-20260922-031",
        {"code": "TROLLEY_POS_LOSS", "message": "小车位置反馈瞬断",
         "source": "PLC-TR-07", "severity": "medium"})
    svc.triage("AL-20260922-031", "SV-01", V.CAUSE_MECHANICAL, "medium",
               rationale="编码器瞬断，按机械故障处置")
    svc.open_work_order("AL-20260922-031", "plan-trolley", "SV-01")
    p("RMG-07 同窗口并行工单开启成功（资源锁互不交集）")

    # ---------------- 5. 临时旁路：窄白名单 ----------------

    tick(3)
    if verbose:
        _line("09:05 船期紧张，签发 20 分钟窄白名单旁路（仅远程手动），调度可见")
    svc.grant_bypass("AL-20260922-018", V.BYPASS_SCOPE_MANUAL, "SV-01",
                     "C-SUP-01", "TL-AUTH-02",
                     reason="配合邻船移舱，仅允许远程手动操作", ttl_minutes=20)
    if verbose:
        _status(svc)

    tick(5)
    if verbose:
        _line("09:10 移舱完成，解除旁路（不允许旁路带入复测）")
    svc.lift_bypass("bp-AL-20260922-018-01", "SV-01")

    # ---------------- 6. 离线补录换件 ----------------

    tick(30)
    if verbose:
        _line("09:40 网络恢复，补录 09:12 实际完成的换件（保留实际发生时间）")
    svc.replace_part("AL-20260922-018", "ST-NEW-1", "spreader.height_sensor",
                     "TC-01", "C-TC-01", "TL-TQ-07",
                     occurred_at="2026-09-22T09:12:00Z")
    p("换件事件 occurred_at=09:12（实际），recorded_at=09:40（补录），双时间戳入链")

    tick(5)
    if verbose:
        _line("09:45 换件后校准")
    svc.record_calibration(
        "RMG-03", "spreader.height_sensor", "TC-01", "C-TC-01", "pass",
        {"height_mm": [0, 500, 12000], "deviation_mm": 1.2}, "STD-LASER-03",
        alarm_id="AL-20260922-018", wo_id="wo-AL-20260922-018-01",
        tool_id="TL-CAL-09")

    tick(5)
    if verbose:
        _line("09:50 复测通过，等待放行")
    svc.retest("AL-20260922-018", "pass",
               {"联锁动作": 10, "最大偏差_mm": 1.8}, "STD-LASER-03",
               "TC-01", "C-TC-01", "TL-CAL-09", notes="连续 10 次联锁动作正常")
    if verbose:
        _status(svc)

    # ---------------- 7. 召回联动 ----------------

    tick(2)
    if verbose:
        _line("09:52 供应商召回：P-HS-770 批次 B2026-03，关联设备能力随即暂停")
    svc.issue_recall("RC-2026-041", "P-HS-770", ["B2026-03"],
                     reason="温补电路在高温下存在零漂风险", by="SV-01")
    if verbose:
        _status(svc)

    # ---------------- 8. 放行被闸门拒绝 ----------------

    tick(3)
    if verbose:
        _line("09:55 复核请求放行——闸门一次性列出全部缺口并留拒绝签署")
    try:
        svc.request_release("AL-20260922-018", "RV-01", "C-RV-01",
                            rationale="初版复核：依据换件校准与复测")
    except ReleaseGateError as exc:
        p(f"放行被拒：{exc}")

    # ---------------- 9. 召回处置：新批次换件 ----------------

    tick(5)
    if verbose:
        _line("10:00 取消旧工单，启用未受影响批次 B2026-04 重新处置")
    svc.cancel_work_order("AL-20260922-018", "wo-AL-20260922-018-01",
                          "SV-01", reason="换入件命中召回 RC-2026-041")
    svc.receive_stock("ST-NEW-2", "P-HS-770", "SN-NEW-9188", "B2026-04")
    svc.open_work_order("AL-20260922-018", "plan-mech", "SV-01")
    svc.reserve_part("AL-20260922-018", "ST-NEW-2", "SV-01")

    tick(5)
    svc.replace_part("AL-20260922-018", "ST-NEW-2", "spreader.height_sensor",
                     "TC-01", "C-TC-01", "TL-TQ-07")
    tick(7)
    svc.record_calibration(
        "RMG-03", "spreader.height_sensor", "TC-01", "C-TC-01", "pass",
        {"height_mm": [0, 500, 12000], "deviation_mm": 0.9}, "STD-LASER-03",
        alarm_id="AL-20260922-018", wo_id="wo-AL-20260922-018-02",
        tool_id="TL-CAL-09")
    tick(6)
    if verbose:
        _line("10:18 再次复测")
    svc.retest("AL-20260922-018", "pass",
               {"联锁动作": 12, "最大偏差_mm": 1.4}, "STD-LASER-03",
               "TC-01", "C-TC-01", "TL-CAL-09", notes="更换非召回批次后 12 次全通过")

    # ---------------- 10. 放行与溯源 ----------------

    tick(2)
    if verbose:
        _line("10:20 最终放行：闸门全绿，恢复自动能力")
    result = svc.request_release(
        "AL-20260922-018", "RV-01", "C-RV-01",
        rationale="已排除未确认参数；实测为机械故障，两次换件中首件命中召回已拆除，"
                  "在装 SN-NEW-9188（B2026-04）校准合格、复测 12 次通过，"
                  "复核确认恢复自动能力。")
    p(f"放行 {result['release_id']}，恢复："
      f"{'、'.join(CAPABILITY_LABELS[c] for c in result['resumed_caps'])}")
    if verbose:
        _status(svc)
        _line("放行溯源：版本 / 人员 / 工具 / 部件 / 复核理由")
        trace = svc.release_trace("AL-20260922-018")
        _print_trace(trace)
        _line("完整性校验")
        print(svc.verify_integrity())

    return svc


def _print_trace(trace: dict) -> None:
    print(f"放行单号：{trace['release_id']}　设备：{trace['machine']['name']}")
    print(f"放行人：{trace['reviewer']['name']}（凭证 {trace['reviewer']['cert_id']}）"
          f"　时间：{trace['released_at']}")
    print("软件版本：")
    for s in trace["software"]:
        print(f"  - {s['controller']} {s['version']} / 参数集 {s['params_version']}"
              f"（已确认）")
    print("在装部件：")
    for c in trace["components_after"]:
        print(f"  - {c['slot']} → {c['part_no']} {c['serial']} 批次{c['batch']}"
              f"（{c['fitted_at']} 由 {c['fitted_by']} 装机）")
    print("处置动作签署链：")
    for a in trace["actions"]:
        print(f"  - {a['at']} {V.ACTION_LABELS.get(a['kind'], a['kind'])}"
              f"　{a['by']}　工具 {a['tool_id']}　凭证 {a['cert_id']}")
    print("闸门检查：")
    for name, chk in trace["gate_checks"].items():
        print(f"  - [{'通过' if chk['ok'] else '未过'}] {name}：{chk['detail']}")
    print(f"复核理由：{trace['review_rationale']}")
    print("恢复能力：" + "、".join(
        CAPABILITY_LABELS.get(c, c) for c in trace["resumed_caps"]))


def run_demo(data_dir: str = "data", reset: bool = True) -> None:
    from pathlib import Path

    log_path = Path(data_dir) / "events.jsonl"
    if reset and log_path.exists():
        log_path.unlink()
    build_demo(data_dir=data_dir, verbose=True)
    print(f"\n事件日志与密钥已写入 {data_dir}/，可使用 CLI 继续查询。")


if __name__ == "__main__":
    import sys

    run_demo(sys.argv[1] if len(sys.argv) > 1 else "data")
