"""事件折叠器：把仅追加日志重放成当前对象图。"""

from __future__ import annotations

from ..core.eventlog import EventLog
from . import vocab as V
from .models import (
    AlarmCase,
    Bypass,
    CalibrationRecord,
    Cert,
    ComponentInstall,
    DataAccess,
    DiagSession,
    Machine,
    Person,
    ReleaseRecord,
    SignedAction,
    SoftwareState,
    StockItem,
    Window,
    WorkOrder,
)


class Registry:
    def __init__(self):
        self.machines: dict[str, Machine] = {}
        self.people: dict[str, Person] = {}
        self.stock: dict[str, StockItem] = {}
        self.plans: dict[str, dict] = {}
        self.recalls: list = []
        # alarm_id -> (machine_id, AlarmCase) 便于跨设备查找
        self.alarm_index: dict[str, str] = {}

    def machine_of_alarm(self, alarm_id: str) -> Machine:
        return self.machines[self.alarm_index[alarm_id]]

    def find_recall(self, part_no: str, batch: str):
        for r in self.recalls:
            if r.matches(part_no, batch):
                return r
        return None


def _cert(d: dict) -> Cert:
    return Cert(
        cert_id=d["cert_id"], scope=d["scope"],
        valid_from=d["valid_from"], valid_to=d["valid_to"],
        allowed_tools=list(d.get("allowed_tools", [])),
        actions=list(d.get("actions", [])),
    )


def project(log: EventLog) -> Registry:
    reg = Registry()
    for ev in log:
        _apply(reg, ev)
    return reg


def _apply(reg: Registry, ev) -> None:  # noqa: C901 - 事件折叠分发，分支直观
    d, t = ev.data, ev.type
    at = ev.occurred_at

    if t == V.MACHINE_REGISTERED:
        reg.machines[d["machine_id"]] = Machine(
            machine_id=d["machine_id"], name=d["name"], model=d["model"],
            registered_at=at, config=d.get("config", {}),
        )

    elif t == V.PERSON_REGISTERED:
        reg.people[d["person_id"]] = Person(
            person_id=d["person_id"], name=d["name"], role=d["role"],
            certs=[_cert(c) for c in d.get("certs", [])],
        )

    elif t == V.WINDOW_DEFINED:
        m = reg.machines[d["machine_id"]]
        if d.get("cancel_window_id"):
            w = next(x for x in m.windows if x.window_id == d["cancel_window_id"])
            w.cancelled = True
        else:
            m.windows.append(Window(
                window_id=d["window_id"], machine_id=d["machine_id"],
                start_at=d["start_at"], end_at=d["end_at"],
                kind=d.get("kind", "scheduled"), note=d.get("note", ""),
            ))

    elif t == V.PLAN_PUBLISHED:
        reg.plans[d["plan_id"]] = dict(d, published_at=at)

    elif t == V.STOCK_RECEIVED:
        reg.stock[d["stock_id"]] = StockItem(
            stock_id=d["stock_id"], part_no=d["part_no"], serial=d["serial"],
            batch=d["batch"], received_at=at,
        )

    elif t == V.COMPONENT_FITTED:
        m = reg.machines[d["machine_id"]]
        slot = d["slot"]
        old = m.components.get(slot)
        if old:
            old.active = False
            old.removed_at = at
        inst = ComponentInstall(
            slot=slot, serial=d["serial"], part_no=d["part_no"],
            batch=d["batch"], fitted_at=at, fitted_by=d["by"],
            stock_id=d["stock_id"],
        )
        m.components[slot] = inst
        m.component_history.append(inst)
        st = reg.stock.get(d["stock_id"])
        if st:
            st.status = "installed"
            st.reserved_for = None

    elif t == V.SOFTWARE_VERSION_SET:
        m = reg.machines[d["machine_id"]]
        m.software[d["controller"]] = SoftwareState(
            controller=d["controller"], version=d["version"],
            params_version=d.get("params_version", "n/a"),
            deployed_at=at, deployed_by=d["by"],
            confirmed=d.get("confirmed", True),
        )

    elif t == V.PARAMS_DEPLOYED:
        m = reg.machines[d["machine_id"]]
        sw = m.software[d["controller"]]
        sw.previous_params_version = sw.params_version
        sw.params_version = d["params_version"]
        sw.deployed_at = at
        sw.deployed_by = d["by"]
        sw.confirmed = d.get("confirmed", False)

    elif t == V.CALIBRATION_RECORDED:
        m = reg.machines[d["machine_id"]]
        m.calibrations.append(CalibrationRecord(
            cal_id=d["cal_id"], slot=d["slot"], serial=d["serial"],
            at=at, by=d["by"], cert_id=d["cert_id"],
            result=d["result"], readings=d.get("readings", {}),
            standard=d["standard"],
        ))
        if d.get("wo_id"):
            wo = next(w for w in m.alarms[d["alarm_id"]].work_orders
                      if w.wo_id == d["wo_id"])
            wo.status = "in_progress"
            wo.actions.append(SignedAction(
                kind=V.ACTION_CALIBRATE, at=at, by=d["by"],
                tool_id=d.get("tool_id", ""), cert_id=d["cert_id"],
                detail={"slot": d["slot"], "serial": d["serial"],
                        "result": d["result"], "standard": d["standard"],
                        "readings": d.get("readings", {})},
                event_seq=ev.seq,
            ))

    elif t == V.ALARM_RAISED:
        m = reg.machines[d["machine_id"]]
        case = AlarmCase(alarm_id=d["alarm_id"], machine_id=d["machine_id"],
                         raised_at=at, raw=d.get("raw", {}), state=V.ALARM_OPEN)
        m.alarms[d["alarm_id"]] = case
        reg.alarm_index[d["alarm_id"]] = d["machine_id"]
        m.state = V.ST_ALARMED

    elif t == V.TRIAGE_CLASSIFIED:
        case = reg.machines[d["machine_id"]].alarms[d["alarm_id"]]
        case.cause = d["cause"]
        case.cause_confidence = d.get("confidence")
        case.triaged_at = at
        case.triaged_by = d["by"]
        case.state = V.ALARM_TRIAGED
        reg.machines[d["machine_id"]].state = V.ST_DIAGNOSING

    elif t == V.DIAG_SESSION_OPENED:
        case = reg.machines[d["machine_id"]].alarms[d["alarm_id"]]
        case.sessions.append(DiagSession(
            session_id=d["session_id"], alarm_id=d["alarm_id"],
            opened_at=at, opened_by=d["by"], purpose=d["purpose"],
            data_scopes=list(d["data_scopes"]),
        ))

    elif t == V.DIAG_DATA_ACCESSED:
        case = reg.machines[d["machine_id"]].alarms[d["alarm_id"]]
        sess = _find_session(case, d["session_id"])
        sess.accesses.append(DataAccess(at=at, scope=d["scope"], purpose=d["purpose"]))

    elif t == V.DIAG_SESSION_CLOSED:
        case = reg.machines[d["machine_id"]].alarms[d["alarm_id"]]
        _find_session(case, d["session_id"]).closed_at = at

    elif t == V.WORK_ORDER_OPENED:
        m = reg.machines[d["machine_id"]]
        case = m.alarms[d["alarm_id"]]
        wo = WorkOrder(
            wo_id=d["wo_id"], alarm_id=d["alarm_id"], plan_id=d["plan_id"],
            opened_at=at, opened_by=d["by"], locks=list(d["locks"]),
            estimated_minutes=d["estimated_minutes"], status="open",
        )
        case.work_orders.append(wo)
        case.state = V.ALARM_IN_REPAIR
        m.state = V.ST_MAINTENANCE

    elif t == V.PART_RESERVED:
        wo = _find_wo(reg, d)
        wo.reserved_parts.append(d["stock_id"])
        st = reg.stock[d["stock_id"]]
        st.status = "reserved"
        st.reserved_for = d["alarm_id"]

    elif t == V.PART_REPLACED:
        wo = _find_wo(reg, d)
        wo.status = "in_progress"
        wo.actions.append(SignedAction(
            kind=V.ACTION_REPLACE_PART, at=at, by=d["by"],
            tool_id=d["tool_id"], cert_id=d["cert_id"],
            detail={k: d[k] for k in ("slot", "stock_id", "serial",
                                      "part_no", "batch", "removed_serial")},
            event_seq=ev.seq,
        ))

    elif t == V.PARAMS_ROLLED_BACK:
        wo = _find_wo(reg, d)
        wo.status = "in_progress"
        m = reg.machines[d["machine_id"]]
        sw = m.software[d["controller"]]
        sw.params_version = d["to_params_version"]
        sw.confirmed = True
        wo.actions.append(SignedAction(
            kind=V.ACTION_ROLLBACK_PARAMS, at=at, by=d["by"],
            tool_id=d["tool_id"], cert_id=d["cert_id"],
            detail={"controller": d["controller"],
                    "from_params_version": d["from_params_version"],
                    "to_params_version": d["to_params_version"],
                    "software_version": d.get("software_version", sw.version)},
            event_seq=ev.seq,
        ))

    elif t == V.BYPASS_GRANTED:
        m = reg.machines[d["machine_id"]]
        bp = Bypass(
            bypass_id=d["bypass_id"], alarm_id=d["alarm_id"], scope=d["scope"],
            granted_at=at, granted_by=d["by"], expire_at=d["expire_at"],
            reason=d.get("reason", ""),
        )
        m.bypasses.append(bp)
        wo = _find_wo(reg, d)
        wo.status = "in_progress"
        wo.bypass_id = d["bypass_id"]
        wo.actions.append(SignedAction(
            kind=V.ACTION_GRANT_BYPASS, at=at, by=d["by"],
            tool_id=d["tool_id"], cert_id=d["cert_id"],
            detail={"scope": d["scope"], "expire_at": d["expire_at"],
                    "reason": d.get("reason", "")},
            event_seq=ev.seq,
        ))

    elif t == V.BYPASS_LIFTED:
        m = reg.machines[d["machine_id"]]
        bp = next(b for b in m.bypasses if b.bypass_id == d["bypass_id"])
        bp.lifted_at = at

    elif t == V.RETEST_PERFORMED:
        wo = _find_wo(reg, d)
        wo.status = "in_progress"
        action = SignedAction(
            kind=V.ACTION_RETEST, at=at, by=d["by"], tool_id=d["tool_id"],
            cert_id=d["cert_id"],
            detail={"result": d["result"], "readings": d.get("readings", {}),
                    "standard": d.get("standard", ""),
                    "notes": d.get("notes", "")},
            event_seq=ev.seq,
        )
        wo.actions.append(action)
        wo.retest = action
        case = reg.machine_of_alarm(d["alarm_id"]).alarms[d["alarm_id"]]
        case.state = (V.ALARM_PENDING_RETEST if d["result"] == "pass"
                      else V.ALARM_IN_REPAIR)
        reg.machines[d["machine_id"]].state = V.ST_PENDING_RETEST

    elif t == V.WORK_ORDER_CLOSED:
        wo = _find_wo(reg, d)
        wo.status = "closed"
        wo.closed_at = at

    elif t == V.WORK_ORDER_CANCELLED:
        wo = _find_wo(reg, d)
        wo.status = "cancelled"
        wo.closed_at = at
        for stock_id in wo.reserved_parts:
            st = reg.stock.get(stock_id)
            if st and st.status == "reserved":
                st.status = "available"
                st.reserved_for = None
        wo.reserved_parts = []

    elif t == V.RECALL_ISSUED:
        from .models import Recall
        reg.recalls.append(Recall(
            recall_id=d["recall_id"], part_no=d["part_no"],
            batches=list(d["batches"]), reason=d["reason"], issued_at=at,
        ))

    elif t == V.RELEASE_APPROVED:
        m = reg.machines[d["machine_id"]]
        case = m.alarms[d["alarm_id"]]
        rec = ReleaseRecord(
            release_id=d["release_id"], alarm_id=d["alarm_id"],
            machine_id=d["machine_id"], wo_id=d["wo_id"], at=at,
            reviewer=d["reviewer"], reviewer_cert_id=d["reviewer_cert_id"],
            software_version=d["software_version"],
            params_version=d["params_version"],
            resumed_caps=list(d["resumed_caps"]), rationale=d["rationale"],
            gate_checks=d.get("gate_checks", {}), actions=d.get("actions", []),
        )
        case.release = rec
        case.state = V.ALARM_RELEASED
        if not m.active_alarms():
            m.state = V.ST_RELEASED

    elif t == V.RELEASE_REJECTED:
        case = reg.machines[d["machine_id"]].alarms[d["alarm_id"]]
        case.state = V.ALARM_REJECTED
        case.rejected_reason = d["reason"]
        reg.machines[d["machine_id"]].state = V.ST_PENDING_RETEST


def _find_session(case: AlarmCase, session_id: str) -> DiagSession:
    return next(s for s in case.sessions if s.session_id == session_id)


def _find_wo(reg: Registry, d: dict) -> WorkOrder:
    case = reg.machine_of_alarm(d["alarm_id"]).alarms[d["alarm_id"]]
    if "wo_id" in d:
        return next(w for w in case.work_orders if w.wo_id == d["wo_id"])
    return case.active_work_order
