"""维护与能力放行应用服务：所有用例从这里进入，每个业务动作即一条签名事件。"""

from __future__ import annotations

from datetime import timedelta

from ..core.errors import (
    AuthzError,
    ConflictError,
    IntegrityError,
    InventoryError,
    QualificationError,
    ReleaseGateError,
    WorkflowError,
)
from ..core.eventlog import EventLog
from ..core.signing import Keyring
from ..core.time import Clock, SystemClock, parse_iso, to_iso
from ..domain import vocab as V
from ..domain.projector import project
from ..domain.readmodel import evaluate, fleet_view
from . import data as data_slice
from .authz import SYSTEM_ID, SessionToken, issue_token, verify_token
from .gates import evaluate_gate, predicted_resumed_caps

MAX_BACKFILL_HOURS = 72
DEFAULT_SESSION_TTL_MIN = 30


class MaintenanceService:
    def __init__(self, log: EventLog, keyring: Keyring, clock: Clock | None = None):
        self.log = log
        self.keyring = keyring
        self.clock = clock or SystemClock()

    # ================= 内部工具 =================

    @property
    def reg(self):
        return project(self.log)

    def _now(self) -> str:
        return to_iso(self.clock.now())

    def _append(self, type_: str, data: dict, actor: str, *, occurred_at: str | None = None):
        ev = self.log.append(type_, data, actor, occurred_at=occurred_at)
        return ev

    def _machine(self, machine_id: str):
        try:
            return self.reg.machines[machine_id]
        except KeyError:
            raise WorkflowError(f"设备不存在：{machine_id}")

    def _case(self, alarm_id: str):
        reg = self.reg
        try:
            return reg.machine_of_alarm(alarm_id).alarms[alarm_id]
        except KeyError:
            raise WorkflowError(f"报警案卷不存在：{alarm_id}")

    def _person(self, person_id: str):
        try:
            return self.reg.people[person_id]
        except KeyError:
            raise WorkflowError(f"人员不存在：{person_id}")

    def _require_role(self, person_id: str, *roles: str):
        p = self._person(person_id)
        if p.role not in roles:
            raise AuthzError(
                f"{person_id} 角色 {V.ROLE_LABELS.get(p.role, p.role)} 无权执行该操作")
        return p

    def _authorize_action(self, person_id: str, action: str, cert_id: str,
                          tool_id: str, at_iso: str):
        """核验：人存在、凭证在动作时刻有效且覆盖该动作、工具在凭证清单内。"""

        p = self._person(person_id)
        at_dt = parse_iso(at_iso)
        cert = next((c for c in p.certs
                     if c.cert_id == cert_id and c.valid_at(at_dt)
                     and action in c.actions), None)
        if cert is None:
            raise QualificationError(
                f"{person_id} 缺少在 {at_iso} 有效的动作资质："
                f"{V.ACTION_LABELS.get(action, action)}（凭证 {cert_id}）")
        if not tool_id or tool_id not in cert.allowed_tools:
            raise QualificationError(
                f"{person_id} 凭证 {cert_id} 未授权工具 {tool_id} 用于"
                f"{V.ACTION_LABELS.get(action, action)}")
        return p, cert

    def _validate_backfill(self, occurred_at: str | None) -> str:
        at = self._now()
        if occurred_at is None:
            return at
        occ = parse_iso(occurred_at)
        now = parse_iso(at)
        if occ > now:
            raise IntegrityError("补录时间不得晚于当前时间")
        if occ < now - timedelta(hours=MAX_BACKFILL_HOURS):
            raise IntegrityError(
                f"离线补录超出 {MAX_BACKFILL_HOURS} 小时时限，请走专项追溯流程")
        return occurred_at

    def _in_window(self, machine, plan: dict, at_iso: str) -> bool:
        if plan.get("allow_off_window"):
            return True
        at_dt = parse_iso(at_iso)
        return any(w.covers(at_dt) for w in machine.windows)

    def _active_wo(self, alarm_id: str, wo_id: str | None = None):
        case = self._case(alarm_id)
        if case.state == V.ALARM_RELEASED:
            raise WorkflowError(f"案卷 {alarm_id} 已放行，不得再追加检修动作")
        wo = case.active_work_order
        if wo is None:
            raise WorkflowError(f"案卷 {alarm_id} 没有进行中的工单")
        if wo_id and wo.wo_id != wo_id:
            raise WorkflowError(
                f"工单 {wo_id} 非当前活动工单（当前 {wo.wo_id}）")
        return wo

    # ================= 基础登记 =================

    def register_machine(self, machine_id: str, name: str, model: str,
                         config: dict, *, actor: str = SYSTEM_ID):
        if machine_id in self.reg.machines:
            raise WorkflowError(f"设备已登记：{machine_id}")
        return self._append(V.MACHINE_REGISTERED, {
            "machine_id": machine_id, "name": name, "model": model,
            "config": config,
        }, actor)

    def define_window(self, machine_id: str, window_id: str, start_at: str,
                      end_at: str, kind: str = "scheduled", note: str = "",
                      *, actor: str = SYSTEM_ID):
        self._machine(machine_id)
        if parse_iso(start_at) >= parse_iso(end_at):
            raise WorkflowError("停机窗口开始时间必须早于结束时间")
        return self._append(V.WINDOW_DEFINED, {
            "machine_id": machine_id, "window_id": window_id,
            "start_at": start_at, "end_at": end_at,
            "kind": kind, "note": note,
        }, actor)

    def cancel_window(self, machine_id: str, window_id: str, *,
                      actor: str = SYSTEM_ID):
        m = self._machine(machine_id)
        if not any(w.window_id == window_id and not w.cancelled for w in m.windows):
            raise WorkflowError(f"窗口不存在或已取消：{window_id}")
        return self._append(V.WINDOW_DEFINED, {
            "machine_id": machine_id, "cancel_window_id": window_id,
        }, actor)

    def register_person(self, person_id: str, name: str, role: str,
                        certs: list[dict], secret: str | None = None):
        if person_id in self.reg.people:
            raise WorkflowError(f"人员已登记：{person_id}")
        self.keyring.issue(person_id, secret)
        return self._append(V.PERSON_REGISTERED, {
            "person_id": person_id, "name": name, "role": role,
            "certs": certs,
        }, SYSTEM_ID)

    def publish_plan(self, plan_id: str, title: str, applies_cause: str,
                     required_actions: list[str], required_slots: list[str],
                     required_part_no: str | None, estimated_minutes: int,
                     notes: str = "", allow_off_window: bool = False,
                     *, actor: str = SYSTEM_ID):
        if plan_id in self.reg.plans:
            raise WorkflowError(f"方案已发布：{plan_id}")
        return self._append(V.PLAN_PUBLISHED, {
            "plan_id": plan_id, "title": title, "applies_cause": applies_cause,
            "required_actions": required_actions, "required_slots": required_slots,
            "required_part_no": required_part_no,
            "estimated_minutes": estimated_minutes,
            "allow_off_window": allow_off_window, "notes": notes,
        }, actor)

    def receive_stock(self, stock_id: str, part_no: str, serial: str, batch: str,
                      *, actor: str = SYSTEM_ID):
        if stock_id in self.reg.stock:
            raise WorkflowError(f"备件库存号已存在：{stock_id}")
        return self._append(V.STOCK_RECEIVED, {
            "stock_id": stock_id, "part_no": part_no,
            "serial": serial, "batch": batch,
        }, actor)

    def fit_component(self, machine_id: str, slot: str, stock_id: str, by: str):
        m = self._machine(machine_id)
        st = self.reg.stock.get(stock_id)
        if not st or st.status != "available":
            raise InventoryError(f"备件不可领用：{stock_id}")
        if self.reg.find_recall(st.part_no, st.batch):
            raise InventoryError(f"备件 {stock_id} 批次 {st.batch} 已在召回范围，禁止装机")
        return self._append(V.COMPONENT_FITTED, {
            "machine_id": machine_id, "slot": slot, "stock_id": stock_id,
            "part_no": st.part_no, "serial": st.serial, "batch": st.batch,
            "by": by,
        }, by)

    def set_software(self, machine_id: str, controller: str, version: str,
                     params_version: str, by: str, confirmed: bool = True):
        self._machine(machine_id)
        return self._append(V.SOFTWARE_VERSION_SET, {
            "machine_id": machine_id, "controller": controller,
            "version": version, "params_version": params_version,
            "by": by, "confirmed": confirmed,
        }, by)

    def deploy_params(self, machine_id: str, controller: str, params_version: str,
                      by: str, confirmed: bool = False):
        m = self._machine(machine_id)
        if controller not in m.software:
            raise WorkflowError(f"控制器尚未登记：{controller}")
        return self._append(V.PARAMS_DEPLOYED, {
            "machine_id": machine_id, "controller": controller,
            "params_version": params_version, "by": by, "confirmed": confirmed,
        }, by)

    def record_calibration(self, machine_id: str, slot: str, by: str, cert_id: str,
                           result: str, readings: dict, standard: str,
                           *, occurred_at: str | None = None,
                           alarm_id: str | None = None, wo_id: str | None = None,
                           tool_id: str | None = None, historical: bool = False):
        """登记校准。检修中的校准须挂案卷/工单并按动作资质签署。

        historical=True 用于基线资料导入（如历史校准趋势），不受离线补录
        72 小时时限约束，但事件层仍校验"发生不晚于接收"。
        """

        m = self._machine(machine_id)
        inst = m.components.get(slot)
        if not inst:
            raise WorkflowError(f"安装位无在装部件：{slot}")
        at = occurred_at or self._now()
        if not historical and occurred_at:
            self._validate_backfill(occurred_at)
        if occurred_at and parse_iso(at) > parse_iso(self._now()):
            raise IntegrityError("校准时间不得晚于当前时间")
        data = {
            "machine_id": machine_id, "slot": slot, "serial": inst.serial,
            "cal_id": f"cal-{m.machine_id}-{len(m.calibrations) + 1:03d}",
            "by": by, "cert_id": cert_id, "result": result,
            "readings": readings, "standard": standard,
        }
        if alarm_id:
            wo = self._active_wo(alarm_id, wo_id)
            if not self._in_window(m, self.reg.plans[wo.plan_id], at):
                raise WorkflowError("校准动作不在停机窗口内（紧急方案除外）")
            self._authorize_action(by, V.ACTION_CALIBRATE, cert_id, tool_id or "", at)
            data.update({"alarm_id": alarm_id, "wo_id": wo.wo_id,
                         "tool_id": tool_id})
        return self._append(V.CALIBRATION_RECORDED, data, by, occurred_at=at)

    def issue_recall(self, recall_id: str, part_no: str, batches: list[str],
                     reason: str, *, by: str = SYSTEM_ID):
        return self._append(V.RECALL_ISSUED, {
            "recall_id": recall_id, "part_no": part_no,
            "batches": batches, "reason": reason,
        }, by)

    # ================= 报警与分诊 =================

    def raise_alarm(self, machine_id: str, alarm_id: str, raw: dict):
        m = self._machine(machine_id)
        if alarm_id in m.alarms:
            raise WorkflowError(f"报警编号重复：{alarm_id}")
        if not {"code", "message"}.issubset(raw):
            raise WorkflowError("报警原文至少包含 code 与 message")
        return self._append(V.ALARM_RAISED, {
            "machine_id": machine_id, "alarm_id": alarm_id, "raw": raw,
        }, SYSTEM_ID)

    def triage(self, alarm_id: str, by: str, cause: str, confidence: str,
               rationale: str):
        self._require_role(by, V.ROLE_MAINTENANCE_SUPERVISOR)
        case = self._case(alarm_id)
        if case.state != V.ALARM_OPEN:
            raise WorkflowError(f"案卷状态 {case.state} 不允许分诊")
        if cause not in V.CAUSE_LABELS:
            raise WorkflowError(f"未知成因分类：{cause}")
        return self._append(V.TRIAGE_CLASSIFIED, {
            "machine_id": case.machine_id, "alarm_id": alarm_id,
            "by": by, "cause": cause, "confidence": confidence,
            "rationale": rationale,
        }, by)

    # ================= 授权诊断会话 =================

    def open_diag_session(self, alarm_id: str, by: str, purpose: str,
                          data_scopes: list[str], ttl_minutes: int = DEFAULT_SESSION_TTL_MIN):
        person = self._require_role(
            by, V.ROLE_MAINTENANCE_SUPERVISOR, V.ROLE_TECHNICIAN,
            V.ROLE_REMOTE_DRIVER, V.ROLE_REVIEWER)
        case = self._case(alarm_id)
        if case.state == V.ALARM_RELEASED:
            raise WorkflowError("案卷已放行，无需诊断会话")
        unknown = [s for s in data_scopes if s not in V.DATA_LABELS]
        if unknown:
            raise AuthzError(f"申请了未知数据范围：{unknown}")
        if not purpose.strip():
            raise AuthzError("授权会话必须说明取用目的")
        now = self.clock.now()
        session_id = f"sess-{alarm_id}-{len(case.sessions) + 1:02d}"
        self._append(V.DIAG_SESSION_OPENED, {
            "machine_id": case.machine_id, "alarm_id": alarm_id,
            "session_id": session_id, "by": by, "purpose": purpose,
            "data_scopes": data_scopes,
            "role": person.role, "ttl_minutes": ttl_minutes,
        }, by)
        token = SessionToken(
            session_id=session_id, alarm_id=alarm_id, by=by,
            scopes=tuple(data_scopes),
            expire_at=to_iso(now + timedelta(minutes=ttl_minutes)),
        )
        return session_id, issue_token(self.keyring, token)

    def access_diagnostic_data(self, token: str, scope: str, purpose: str) -> dict:
        now_iso = self._now()
        claim = verify_token(self.keyring, token, now_iso)
        case = self._case(claim.alarm_id)
        sess = next((s for s in case.sessions
                     if s.session_id == claim.session_id), None)
        if sess is None or not sess.active:
            raise AuthzError("诊断会话已关闭")
        if scope not in claim.scopes:
            raise AuthzError(
                f"会话 {claim.session_id} 未授权数据范围：{V.DATA_LABELS.get(scope, scope)}")
        if scope not in sess.data_scopes:
            raise AuthzError("数据范围不在批准清单内")
        m = self.reg.machines[case.machine_id]
        payload = data_slice.build_slice(self.reg, m, scope)
        self._append(V.DIAG_DATA_ACCESSED, {
            "machine_id": case.machine_id, "alarm_id": claim.alarm_id,
            "session_id": claim.session_id, "scope": scope,
            "purpose": purpose, "by": claim.by,
        }, claim.by)
        return payload

    def close_diag_session(self, session_id: str, by: str):
        reg = self.reg
        found = None
        for mid, m in reg.machines.items():
            for aid, case in m.alarms.items():
                for s in case.sessions:
                    if s.session_id == session_id:
                        found = (mid, aid, s)
        if not found:
            raise WorkflowError(f"会话不存在：{session_id}")
        mid, aid, sess = found
        if not sess.active:
            raise WorkflowError("会话已关闭")
        if sess.opened_by != by:
            self._require_role(by, V.ROLE_MAINTENANCE_SUPERVISOR)
        return self._append(V.DIAG_SESSION_CLOSED, {
            "machine_id": mid, "alarm_id": aid,
            "session_id": session_id, "by": by,
        }, by)

    # ================= 检修工单与并行控制 =================

    def open_work_order(self, alarm_id: str, plan_id: str, by: str,
                        estimated_minutes: int | None = None,
                        extra_locks: list[str] | None = None):
        self._require_role(by, V.ROLE_MAINTENANCE_SUPERVISOR, V.ROLE_TECHNICIAN)
        case = self._case(alarm_id)
        if case.cause is None:
            raise WorkflowError("报警尚未分诊，不能开工")
        if case.active_work_order:
            raise ConflictError(f"案卷已有进行中的工单：{case.active_work_order.wo_id}")
        plan = self.reg.plans.get(plan_id)
        if not plan:
            raise WorkflowError(f"方案不存在：{plan_id}")
        if plan["applies_cause"] != case.cause:
            raise WorkflowError(
                f"方案适用于 {V.CAUSE_LABELS[plan['applies_cause']]}，"
                f"与分诊 {V.CAUSE_LABELS[case.cause]} 不符")
        m = self.reg.machines[case.machine_id]
        now = self._now()
        if not self._in_window(m, plan, now):
            raise WorkflowError("当前不在停机窗口内，无法开工（紧急方案除外）")

        locks = [f"machine:{m.machine_id}"]
        locks += [f"slot:{m.machine_id}:{s}" for s in plan["required_slots"]]
        locks += extra_locks or []
        locks = sorted(set(locks))

        for other_m in self.reg.machines.values():
            for other_case in other_m.alarms.values():
                other_wo = other_case.active_work_order
                if other_wo and set(other_wo.locks) & set(locks):
                    raise ConflictError(
                        f"资源与并行工单 {other_wo.wo_id} 冲突："
                        f"{'、'.join(sorted(set(other_wo.locks) & set(locks)))}")

        wo_id = f"wo-{alarm_id}-{len(case.work_orders) + 1:02d}"
        return self._append(V.WORK_ORDER_OPENED, {
            "machine_id": case.machine_id, "alarm_id": alarm_id,
            "wo_id": wo_id, "plan_id": plan_id, "by": by,
            "locks": locks,
            "estimated_minutes": estimated_minutes or plan["estimated_minutes"],
        }, by)

    def reserve_part(self, alarm_id: str, stock_id: str, by: str):
        wo = self._active_wo(alarm_id)
        plan = self.reg.plans[wo.plan_id]
        st = self.reg.stock.get(stock_id)
        if not st:
            raise InventoryError(f"备件不存在：{stock_id}")
        if st.status != "available":
            raise ConflictError(f"备件 {stock_id} 状态为 {st.status}，不可预留")
        if plan.get("required_part_no") and st.part_no != plan["required_part_no"]:
            raise WorkflowError(
                f"备件型号 {st.part_no} 与方案要求 {plan['required_part_no']} 不符")
        if self.reg.find_recall(st.part_no, st.batch):
            raise InventoryError(
                f"备件 {stock_id} 批次 {st.batch} 命中召回，禁止预留装机")
        return self._append(V.PART_RESERVED, {
            "machine_id": self._case(alarm_id).machine_id,
            "alarm_id": alarm_id, "wo_id": wo.wo_id,
            "stock_id": stock_id, "by": by,
        }, by)

    def replace_part(self, alarm_id: str, stock_id: str, slot: str, by: str,
                     cert_id: str, tool_id: str, *, occurred_at: str | None = None):
        at = self._validate_backfill(occurred_at)
        case = self._case(alarm_id)
        wo = self._active_wo(alarm_id)
        m = self.reg.machines[case.machine_id]
        plan = self.reg.plans[wo.plan_id]
        if slot not in plan["required_slots"]:
            raise WorkflowError(f"方案 {wo.plan_id} 不涉及安装位 {slot}")
        if not self._in_window(m, plan, at):
            raise WorkflowError("换件不在停机窗口内（紧急方案除外）")
        st = self.reg.stock.get(stock_id)
        if not st or stock_id not in wo.reserved_parts or st.status != "reserved" \
                or st.reserved_for != alarm_id:
            raise InventoryError(f"备件 {stock_id} 未为本工单预留")
        if self.reg.find_recall(st.part_no, st.batch):
            raise InventoryError(f"备件批次 {st.batch} 命中召回，禁止装机")
        self._authorize_action(by, V.ACTION_REPLACE_PART, cert_id, tool_id, at)
        old = m.components.get(slot)
        removed_serial = old.serial if old else None
        self._append(V.PART_REPLACED, {
            "machine_id": m.machine_id, "alarm_id": alarm_id, "wo_id": wo.wo_id,
            "slot": slot, "stock_id": stock_id, "part_no": st.part_no,
            "serial": st.serial, "batch": st.batch,
            "removed_serial": removed_serial,
            "by": by, "cert_id": cert_id, "tool_id": tool_id,
        }, by, occurred_at=at)
        return self._append(V.COMPONENT_FITTED, {
            "machine_id": m.machine_id, "slot": slot, "stock_id": stock_id,
            "part_no": st.part_no, "serial": st.serial, "batch": st.batch,
            "by": by,
        }, by, occurred_at=at)

    def rollback_params(self, alarm_id: str, controller: str, by: str,
                        cert_id: str, tool_id: str):
        case = self._case(alarm_id)
        wo = self._active_wo(alarm_id)
        m = self.reg.machines[case.machine_id]
        plan = self.reg.plans[wo.plan_id]
        sw = m.software.get(controller)
        if not sw:
            raise WorkflowError(f"控制器不存在：{controller}")
        if not sw.previous_params_version:
            raise WorkflowError("没有可回退的上一版参数集")
        if not self._in_window(m, plan, self._now()):
            raise WorkflowError("参数回退不在停机窗口内（紧急方案除外）")
        self._authorize_action(by, V.ACTION_ROLLBACK_PARAMS, cert_id, tool_id, self._now())
        return self._append(V.PARAMS_ROLLED_BACK, {
            "machine_id": m.machine_id, "alarm_id": alarm_id, "wo_id": wo.wo_id,
            "controller": controller, "by": by, "cert_id": cert_id,
            "tool_id": tool_id,
            "from_params_version": sw.params_version,
            "to_params_version": sw.previous_params_version,
            "software_version": sw.version,
        }, by)

    def grant_bypass(self, alarm_id: str, scope: str, by: str, cert_id: str,
                     tool_id: str, reason: str, ttl_minutes: int = 30):
        case = self._case(alarm_id)
        wo = self._active_wo(alarm_id)
        m = self.reg.machines[case.machine_id]
        plan = self.reg.plans[wo.plan_id]
        if scope not in V.BYPASS_SCOPE_LABELS:
            raise WorkflowError(f"旁路范围非法：{scope}")
        now = self.clock.now()
        if not self._in_window(m, plan, to_iso(now)):
            raise WorkflowError("旁路授权必须在停机窗口内签发")
        self._authorize_action(by, V.ACTION_GRANT_BYPASS, cert_id, tool_id, to_iso(now))
        active_bp = [b for b in m.bypasses
                     if b.alarm_id == alarm_id and b.state_at(now) == "active"]
        if active_bp:
            raise ConflictError(f"案卷已有生效旁路：{active_bp[0].bypass_id}")
        bypass_id = f"bp-{alarm_id}-{len(m.bypasses) + 1:02d}"
        return self._append(V.BYPASS_GRANTED, {
            "machine_id": m.machine_id, "alarm_id": alarm_id, "wo_id": wo.wo_id,
            "bypass_id": bypass_id, "scope": scope, "by": by,
            "cert_id": cert_id, "tool_id": tool_id, "reason": reason,
            "expire_at": to_iso(now + timedelta(minutes=ttl_minutes)),
        }, by)

    def lift_bypass(self, bypass_id: str, by: str):
        reg = self.reg
        for m in reg.machines.values():
            for bp in m.bypasses:
                if bp.bypass_id == bypass_id:
                    if bp.lifted_at:
                        raise WorkflowError("旁路已解除")
                    if bp.granted_by != by:
                        self._require_role(by, V.ROLE_MAINTENANCE_SUPERVISOR)
                    return self._append(V.BYPASS_LIFTED, {
                        "machine_id": m.machine_id, "alarm_id": bp.alarm_id,
                        "bypass_id": bypass_id, "by": by,
                    }, by)
        raise WorkflowError(f"旁路不存在：{bypass_id}")

    def retest(self, alarm_id: str, result: str, readings: dict, standard: str,
               by: str, cert_id: str, tool_id: str, notes: str = "",
               *, occurred_at: str | None = None):
        at = self._validate_backfill(occurred_at)
        case = self._case(alarm_id)
        wo = self._active_wo(alarm_id)
        m = self.reg.machines[case.machine_id]
        if result not in ("pass", "fail"):
            raise WorkflowError("复测结果只能是 pass/fail")
        self._authorize_action(by, V.ACTION_RETEST, cert_id, tool_id, at)
        return self._append(V.RETEST_PERFORMED, {
            "machine_id": m.machine_id, "alarm_id": alarm_id, "wo_id": wo.wo_id,
            "result": result, "readings": readings, "standard": standard,
            "by": by, "cert_id": cert_id, "tool_id": tool_id, "notes": notes,
        }, by, occurred_at=at)

    def cancel_work_order(self, alarm_id: str, wo_id: str, by: str, reason: str):
        self._require_role(by, V.ROLE_MAINTENANCE_SUPERVISOR)
        case = self._case(alarm_id)
        wo = next((w for w in case.work_orders if w.wo_id == wo_id and w.active), None)
        if not wo:
            raise WorkflowError(f"没有活动工单 {wo_id}")
        return self._append(V.WORK_ORDER_CANCELLED, {
            "machine_id": case.machine_id, "alarm_id": alarm_id,
            "wo_id": wo_id, "by": by, "reason": reason,
        }, by)

    # ================= 放行 =================

    def request_release(self, alarm_id: str, reviewer_id: str, reviewer_cert_id: str,
                        rationale: str):
        reviewer = self._require_role(reviewer_id, V.ROLE_REVIEWER)
        at = self._now()
        at_dt = parse_iso(at)
        cert = next((c for c in reviewer.certs
                     if c.cert_id == reviewer_cert_id and c.valid_at(at_dt)), None)
        if cert is None:
            raise QualificationError(f"放行复核凭证 {reviewer_cert_id} 已过期或不存在")

        case = self._case(alarm_id)
        m = self.reg.machines[case.machine_id]
        gate = evaluate_gate(self.reg, alarm_id, reviewer_id, at)

        if not gate.ok:
            self._append(V.RELEASE_REJECTED, {
                "machine_id": m.machine_id, "alarm_id": alarm_id,
                "reviewer": reviewer_id, "reviewer_cert_id": reviewer_cert_id,
                "rationale": rationale,
                "reason": "；".join(gate.missing),
                "gate_checks": gate.checks,
            }, reviewer_id)
            raise ReleaseGateError("放行条件未满足：" + "；".join(gate.missing))

        wo = case.active_work_order
        resumed = predicted_resumed_caps(self.reg, alarm_id, at)
        action_trace = [
            {"kind": a.kind, "at": a.at, "by": a.by, "tool_id": a.tool_id,
             "cert_id": a.cert_id, "detail": a.detail}
            for a in wo.actions
        ]
        sw_versions = {s.controller: {"version": s.version,
                                      "params_version": s.params_version}
                       for s in m.software.values()}
        release_id = f"rel-{alarm_id}"
        self._append(V.WORK_ORDER_CLOSED, {
            "machine_id": m.machine_id, "alarm_id": alarm_id,
            "wo_id": wo.wo_id, "by": reviewer_id,
        }, reviewer_id)
        self._append(V.RELEASE_APPROVED, {
            "machine_id": m.machine_id, "alarm_id": alarm_id,
            "wo_id": wo.wo_id, "release_id": release_id,
            "reviewer": reviewer_id, "reviewer_cert_id": reviewer_cert_id,
            "software_version": ";".join(
                f"{c}:{v['version']}/参数{v['params_version']}"
                for c, v in sorted(sw_versions.items())),
            "params_version": ";".join(
                f"{c}:{v['params_version']}" for c, v in sorted(sw_versions.items())),
            "resumed_caps": resumed,
            "rationale": rationale,
            "gate_checks": gate.checks,
            "actions": action_trace,
        }, reviewer_id)
        return {"release_id": release_id, "gate_checks": gate.checks,
                "resumed_caps": resumed}

    def release_trace(self, alarm_id: str) -> dict:
        """最终放行溯源：哪个版本、谁用什么工具处理了哪个部件、复核为何同意。"""

        case = self._case(alarm_id)
        if not case.release:
            raise WorkflowError(f"案卷 {alarm_id} 尚无放行结论")
        rec = case.release
        m = self.reg.machines[case.machine_id]
        return {
            "release_id": rec.release_id,
            "alarm_id": rec.alarm_id,
            "machine": {"id": m.machine_id, "name": m.name},
            "released_at": rec.at,
            "reviewer": {"id": rec.reviewer, "cert_id": rec.reviewer_cert_id,
                         "name": self.reg.people[rec.reviewer].name},
            "review_rationale": rec.rationale,
            "software": [
                {"controller": s.controller, "version": s.version,
                 "params_version": s.params_version, "confirmed": s.confirmed}
                for s in m.software.values()
            ],
            "components_after": [
                {"slot": c.slot, "part_no": c.part_no, "serial": c.serial,
                 "batch": c.batch, "fitted_at": c.fitted_at, "fitted_by": c.fitted_by}
                for c in m.components.values()
            ],
            "actions": rec.actions,
            "gate_checks": rec.gate_checks,
            "resumed_caps": rec.resumed_caps,
        }

    # ================= 调度视图 =================

    def _eval_at(self, at_iso: str | None) -> str:
        if at_iso:
            return at_iso
        if self.log:
            last = self.log.last()
            if last:
                return last.recorded_at
        return self._now()

    def machine_status(self, machine_id: str, at_iso: str | None = None):
        at = at_iso or self._now()
        return evaluate(self.reg, self._machine(machine_id), at)

    def fleet_status(self, at_iso: str | None = None) -> list:
        at = at_iso or self._now()
        return fleet_view(self.reg, at)

    def verify_integrity(self):
        self.log.verify()
        return {"events": len(self.log), "status": "ok"}
