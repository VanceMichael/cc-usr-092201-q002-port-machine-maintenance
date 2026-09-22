"""派生读模型：能力挂起、可承担动作、预计恢复时间（ETR）。

调度席不看笼统的"维修中"，而是拿到：
- suspensions：逐条挂起原因（报警/工单/旁路/召回/未确认参数/复测状态）；
- available_caps：此刻仍可承担的动作；
- etr：预计恢复时间及其计算依据，等待窗口/召回处置时明确返回 None 与原因。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from ..core.time import parse_iso, to_iso
from . import vocab as V
from .models import Machine
from .models import BYPASS_SCOPE_CAPS, HARD_BLOCK_CAPS
from .projector import Registry

RETEST_BUFFER_MIN = 15        # 待复测默认保留的收尾时间
MIN_REMAINING_MIN = 10        # 工单剩余时间下限，避免 ETR 落在当下


@dataclass
class Suspension:
    code: str
    detail: str
    blocks: set[str] = field(default_factory=lambda: set(HARD_BLOCK_CAPS))


@dataclass
class MachineStatus:
    machine_id: str
    name: str
    state: str
    open_alarm_ids: list[str]
    suspensions: list[Suspension]
    available_caps: list[str]
    etr: Optional[str]
    etr_basis: str

    @property
    def operational(self) -> bool:
        return not self.suspensions


def _current_window(m: Machine, at_dt):
    for w in m.windows:
        if w.covers(at_dt):
            return w
    return None


def _next_window(m: Machine, at_dt):
    upcoming = [w for w in m.windows
                if not w.cancelled and parse_iso(w.start_at) > at_dt]
    return min(upcoming, key=lambda w: parse_iso(w.start_at), default=None)


def _recall_hits(reg: Registry, m: Machine):
    hits = []
    for inst in m.components.values():
        recall = reg.find_recall(inst.part_no, inst.batch)
        if recall:
            hits.append((inst, recall))
    return hits


def evaluate(reg: Registry, m: Machine, at_iso: str) -> MachineStatus:
    at_dt = parse_iso(at_iso)
    susp: list[Suspension] = []
    bypass_caps: set[str] = set()
    etr_dt = None
    etr_basis = "设备运行正常"

    open_alarms = m.active_alarms()

    # 召回：在装部件命中召回批次 → 相关能力立即暂停（与工单状态无关）
    for inst, recall in _recall_hits(reg, m):
        susp.append(Suspension(
            V.SUSP_RECALL,
            f"安装位 {inst.slot} 部件 {inst.part_no}/{inst.serial} "
            f"批次 {inst.batch} 命中召回 {recall.recall_id}：{recall.reason}",
        ))

    # 未确认软件参数
    unconfirmed = [s for s in m.software.values() if not s.confirmed]
    for sw in unconfirmed:
        susp.append(Suspension(
            V.SUSP_UNCONFIRMED,
            f"{sw.controller} 参数集 {sw.params_version} 未经确认（现版本 {sw.version}）",
        ))

    # 旁路：生效中授予窄白名单；超时未解除升级为硬挂起
    expired_bypass = False
    for bp in m.bypasses:
        state = bp.state_at(at_dt)
        if state == "active":
            bypass_caps |= BYPASS_SCOPE_CAPS.get(bp.scope, set())
            susp.append(Suspension(
                V.SUSP_BYPASS,
                f"旁路 {bp.bypass_id} 生效至 {bp.expire_at}，"
                f"仅限 {V.BYPASS_SCOPE_LABELS.get(bp.scope, bp.scope)} 范围内动作",
            ))
        elif state == "expired":
            expired_bypass = True
            susp.append(Suspension(
                V.SUSP_BYPASS_EXPIRED,
                f"旁路 {bp.bypass_id} 已于 {bp.expire_at} 超时且未解除",
            ))

    # 报警案卷级挂起与 ETR
    work_remaining_min = 0
    awaiting_review = False
    recall_await = any(s.code == V.SUSP_RECALL for s in susp)

    for case in open_alarms:
        susp.append(Suspension(
            V.SUSP_ALARM,
            f"报警 {case.alarm_id} 未关闭"
            + (f"（{V.CAUSE_LABELS[case.cause]}）" if case.cause else "（尚未分诊）"),
        ))
        wo = case.active_work_order
        if wo:
            susp.append(Suspension(
                V.SUSP_WORK_ORDER,
                f"工单 {wo.wo_id} 占用资源：{'、'.join(wo.locks)}",
            ))
            retest_passed = bool(
                wo.retest and wo.retest.detail.get("result") == "pass")
            if retest_passed:
                awaiting_review = True
            else:
                elapsed = max(
                    0.0, (at_dt - parse_iso(wo.opened_at)).total_seconds() / 60)
                work_remaining_min += max(
                    MIN_REMAINING_MIN, wo.estimated_minutes - elapsed)
                if wo.retest is None:
                    susp.append(Suspension(
                        V.SUSP_RETEST_PENDING,
                        f"工单 {wo.wo_id} 尚未完成复测",
                    ))
                else:
                    susp.append(Suspension(
                        V.SUSP_RETEST_FAILED,
                        f"工单 {wo.wo_id} 复测未通过："
                        f"{wo.retest.detail.get('notes', '')}",
                    ))

    if open_alarms:
        win = _current_window(m, at_dt)
        if work_remaining_min > 0:
            if win:
                etr_dt = at_dt + timedelta(minutes=work_remaining_min)
                etr_basis = (f"工单剩余约 {int(work_remaining_min)} 分钟"
                             f"（窗口 {win.window_id} 内连续作业）")
            else:
                nxt = _next_window(m, at_dt)
                if nxt:
                    etr_dt = parse_iso(nxt.start_at) + timedelta(
                        minutes=work_remaining_min)
                    etr_basis = (
                        f"当前无停机窗口，约 {int(work_remaining_min)} 分钟作业量"
                        f"排入窗口 {nxt.window_id}（{nxt.start_at} 起）")
                else:
                    etr_basis = "等待停机窗口确认"
        elif awaiting_review and not recall_await:
            etr_dt = at_dt + timedelta(minutes=RETEST_BUFFER_MIN)
            etr_basis = "复测已通过，等待复核放行收尾"
        elif not recall_await:
            nxt = _next_window(m, at_dt)
            if nxt:
                etr_dt = parse_iso(nxt.start_at)
                etr_basis = (f"等待停机窗口 {nxt.window_id}"
                             f"（{nxt.start_at} 起）恢复处置")
            else:
                etr_dt = None
                etr_basis = "等待停机窗口确认，恢复时间待定"

    if recall_await:
        etr_dt = None
        etr_basis = "命中召回批次，等待部件处置，恢复时间待定"

    # 可承担动作：自动能力全部被硬挂起；生效旁路给出窄白名单
    if open_alarms or susp:
        available = sorted(bypass_caps)
    else:
        available = sorted(HARD_BLOCK_CAPS)

    return MachineStatus(
        machine_id=m.machine_id, name=m.name, state=m.state,
        open_alarm_ids=[c.alarm_id for c in open_alarms],
        suspensions=susp,
        available_caps=available,
        etr=to_iso(etr_dt) if etr_dt else None,
        etr_basis=etr_basis,
    )


def fleet_view(reg: Registry, at_iso: str) -> list[MachineStatus]:
    return [evaluate(reg, m, at_iso) for m in reg.machines.values()]
