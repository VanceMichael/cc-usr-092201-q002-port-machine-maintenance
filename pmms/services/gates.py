"""放行闸门：最终一次放行为何可以恢复，全部条件逐条可核。

闸门只做判定与取证，不改状态；service 依据结果追加 RELEASE_APPROVED/REJECTED。
每条检查带 ok 与依据，失败时一次性返回全部缺口，避免逐个试错。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from ..core.time import parse_iso
from ..domain import vocab as V
from ..domain.readmodel import evaluate
from ..domain.projector import Registry


@dataclass
class GateResult:
    ok: bool
    checks: dict
    missing: list[str]


def _check(name: str, ok: bool, detail: str) -> tuple[str, bool, str]:
    return name, ok, detail


def evaluate_gate(reg: Registry, alarm_id: str, reviewer_id: str, at_iso: str) -> GateResult:
    machine = reg.machine_of_alarm(alarm_id)
    case = machine.alarms[alarm_id]
    checks: dict = {}
    missing: list[str] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks[name] = {"ok": ok, "detail": detail}
        if not ok:
            missing.append(f"{name}: {detail}")

    # 1. 必须已分诊
    add(*_check(
        "triaged", case.cause is not None,
        f"成因：{V.CAUSE_LABELS.get(case.cause, '未分诊')}"
        + (f"（置信度 {case.cause_confidence}）" if case.cause_confidence else ""),
    ))

    wo = case.active_work_order
    add("work_order", wo is not None,
        f"工单 {wo.wo_id}" if wo else "没有进行中的检修工单")
    if not wo:
        return GateResult(False, checks, missing)

    plan = reg.plans.get(wo.plan_id, {})

    # 2. 方案要求的动作全部完成（换件/回退/旁路/复测/校准）
    done_kinds = {a.kind for a in wo.actions}
    required = set(plan.get("required_actions", []))
    pending_actions = required - done_kinds
    add("required_actions", not pending_actions,
        "已完成：" + "、".join(V.ACTION_LABELS.get(k, k) for k in sorted(done_kinds))
        if not pending_actions
        else "缺少：" + "、".join(V.ACTION_LABELS.get(k, k) for k in sorted(pending_actions)))

    # 3. 旁路必须已解除（超时未解除同样阻断）
    bypasses = [b for b in machine.bypasses if b.alarm_id == alarm_id]
    unlifted = [b for b in bypasses if not b.lifted_at]
    add("bypass_lifted", not unlifted,
        "全部旁路已解除" if not unlifted
        else "未解除旁路：" + "、".join(
            f"{b.bypass_id}({V.BYPASS_SCOPE_LABELS.get(b.scope, b.scope)}"
            + ("已超时" if b.state_at(parse_iso(at_iso)) == "expired" else "生效中") + ")"
            for b in unlifted))

    # 4. 复测通过且使用了登记基准器具
    retest = wo.retest
    retest_ok = bool(retest and retest.detail.get("result") == "pass")
    add("retest_pass", retest_ok,
        f"{retest.at} 由 {retest.by} 使用 {retest.tool_id} 复测通过"
        if retest_ok else
        ("复测未通过：" + retest.detail.get("notes", "") if retest else "尚未复测"))

    # 5. 软件版本与参数集：当前版本明确、参数已确认
    sw_bad = [f"{s.controller}@{s.version}/参数 {s.params_version}"
              for s in machine.software.values() if not s.confirmed]
    add("params_confirmed", not sw_bad,
        "全部控制器参数集均已确认" if not sw_bad
        else "未确认参数集：" + "、".join(sw_bad))

    # 6. 在装部件无召回命中
    recall_hits = []
    for inst in machine.components.values():
        hit = reg.find_recall(inst.part_no, inst.batch)
        if hit:
            recall_hits.append(f"{inst.slot}/{inst.serial} 批次{inst.batch}→{hit.recall_id}")
    add("no_recall_hit", not recall_hits,
        "在装部件均无召回命中" if not recall_hits
        else "命中召回：" + "、".join(recall_hits))

    # 7. 换件后的校准闭环
    replaced_slots = {a.detail["slot"] for a in wo.actions
                      if a.kind == V.ACTION_REPLACE_PART}
    cal_gaps = []
    for slot in sorted(replaced_slots):
        last_replace = max(a.at for a in wo.actions
                           if a.kind == V.ACTION_REPLACE_PART and a.detail["slot"] == slot)
        cals = [c for c in machine.calibrations
                if c.slot == slot and c.at > last_replace and c.result == "pass"
                and c.serial == machine.components[slot].serial]
        if not cals:
            cal_gaps.append(slot)
    cal_required = V.ACTION_CALIBRATE in required or replaced_slots
    add("post_repair_calibration", not (cal_required and cal_gaps),
        "换件位均有合格校准" if not cal_gaps else "缺少换件后合格校准：" + "、".join(cal_gaps))

    # 8. 每个签署动作的资质/工具在动作发生时有效
    qual_gaps = []
    for a in wo.actions:
        person = reg.people.get(a.by)
        if not person:
            qual_gaps.append(f"{V.ACTION_LABELS.get(a.kind, a.kind)} 签署人 {a.by} 不存在")
            continue
        at_dt = parse_iso(a.at)
        cert = next((c for c in person.certs
                     if c.cert_id == a.cert_id and c.valid_at(at_dt)
                     and a.kind in c.actions and a.tool_id in c.allowed_tools), None)
        if cert is None:
            qual_gaps.append(
                f"{a.at} {V.ACTION_LABELS.get(a.kind, a.kind)} 由 {a.by} "
                f"凭证 {a.cert_id}/工具 {a.tool_id} 不在有效授权内")
    add("action_qualifications", not qual_gaps,
        "全部动作签署资质与工具核验通过" if not qual_gaps
        else "；".join(qual_gaps))

    # 9. 复核独立性：放行人不得是本案卷任一检修动作的签署人
    signers = {a.by for a in wo.actions}
    add("reviewer_independent", reviewer_id not in signers,
        f"复核人 {reviewer_id} 独立于检修签署人" if reviewer_id not in signers
        else f"复核人 {reviewer_id} 参与过检修签署，不得自行放行")

    return GateResult(not missing, checks, missing)


def predicted_resumed_caps(reg: Registry, alarm_id: str, at_iso: str) -> list[str]:
    """模拟放行后重算设备能力，作为放行记录里的 resumed_caps。"""

    sim = copy.deepcopy(reg)
    m = sim.machine_of_alarm(alarm_id)
    case = m.alarms[alarm_id]
    case.state = V.ALARM_RELEASED
    if not m.active_alarms():
        m.state = V.ST_RELEASED
    status = evaluate(sim, m, at_iso)
    return status.available_caps
