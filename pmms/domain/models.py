"""投影读模型：事件折叠后的设备、部件、软件、人员、工单、报警案卷。

投影只负责"把事件变成当前状态"，业务规则在 services 层；所有派生结论
（挂起原因、可承担动作、ETR）都可由事件重算，不存独立结论。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..core.time import parse_iso
from . import vocab as V


# ---------- 能力目录 ----------

CAP_AUTO_HOIST = "auto_hoist"          # 自动起升
CAP_AUTO_TROLLEY = "auto_trolley"      # 自动小车
CAP_AUTO_SPREADER = "auto_spreader"    # 自动吊具
CAP_AUTO_TRAVEL = "auto_travel"        # 自动行走
CAP_REMOTE_MANUAL = "remote_manual"    # 远程手动（仅旁路授权时）
CAP_SLOW_TRAVEL = "slow_travel"        # 低速移机（仅旁路授权时）

CAPABILITY_LABELS = {
    CAP_AUTO_HOIST: "自动起升",
    CAP_AUTO_TROLLEY: "自动小车",
    CAP_AUTO_SPREADER: "自动吊具锁止",
    CAP_AUTO_TRAVEL: "自动行走",
    CAP_REMOTE_MANUAL: "远程手动操作",
    CAP_SLOW_TRAVEL: "低速移机",
}

# 硬性挂起原因覆盖的自动能力集
HARD_BLOCK_CAPS = {CAP_AUTO_HOIST, CAP_AUTO_TROLLEY, CAP_AUTO_SPREADER, CAP_AUTO_TRAVEL}

BYPASS_SCOPE_CAPS = {
    V.BYPASS_SCOPE_MANUAL: {CAP_REMOTE_MANUAL},
    V.BYPASS_SCOPE_SLOW_TRAVEL: {CAP_SLOW_TRAVEL},
}

# ---------- 人员与资质 ----------

@dataclass
class Cert:
    cert_id: str
    scope: str                 # 资质范围，如 component:height_sensor / electrical / software
    valid_from: str
    valid_to: str
    allowed_tools: list[str] = field(default_factory=list)  # 允许使用的工具；空=不允许任何工具
    actions: list[str] = field(default_factory=list)        # 可签署动作，见 vocab.ACTION_*

    def valid_at(self, at: datetime) -> bool:
        return parse_iso(self.valid_from) <= at <= parse_iso(self.valid_to)


@dataclass
class Person:
    person_id: str
    name: str
    role: str
    certs: list[Cert] = field(default_factory=list)

    def certs_for(self, action: str, at: datetime) -> list[Cert]:
        return [c for c in self.certs if action in c.actions and c.valid_at(at)]


# ---------- 备件与部件 ----------

@dataclass
class StockItem:
    stock_id: str
    part_no: str
    serial: str
    batch: str
    received_at: str
    status: str = "available"   # available / reserved / installed / scrapped
    reserved_for: str | None = None  # alarm_id


@dataclass
class ComponentInstall:
    slot: str                   # 安装位，如 spreader.height_sensor
    serial: str
    part_no: str
    batch: str
    fitted_at: str
    fitted_by: str
    stock_id: str
    active: bool = True
    removed_at: str | None = None


@dataclass
class CalibrationRecord:
    cal_id: str
    slot: str
    serial: str
    at: str
    by: str
    cert_id: str
    result: str                 # pass / drift
    readings: dict
    standard: str               # 基准器具


# ---------- 软件 ----------

@dataclass
class SoftwareState:
    controller: str
    version: str
    params_version: str
    deployed_at: str
    deployed_by: str
    confirmed: bool = False
    previous_params_version: str | None = None


# ---------- 诊断会话 ----------

@dataclass
class DataAccess:
    at: str
    scope: str
    purpose: str


@dataclass
class DiagSession:
    session_id: str
    alarm_id: str
    opened_at: str
    opened_by: str
    purpose: str
    data_scopes: list[str]
    accesses: list[DataAccess] = field(default_factory=list)
    closed_at: str | None = None

    @property
    def active(self) -> bool:
        return self.closed_at is None


# ---------- 检修动作与工单 ----------

@dataclass
class SignedAction:
    """换件/回退/旁路/复测中的一次已签署动作。"""

    kind: str
    at: str
    by: str
    tool_id: str
    cert_id: str
    detail: dict
    event_seq: int


@dataclass
class WorkOrder:
    wo_id: str
    alarm_id: str
    plan_id: str
    opened_at: str
    opened_by: str
    locks: list[str]                     # 占用资源键
    estimated_minutes: int
    status: str = "open"                 # open / in_progress / closed / cancelled
    actions: list[SignedAction] = field(default_factory=list)
    reserved_parts: list[str] = field(default_factory=list)   # stock_id
    bypass_id: str | None = None
    retest: SignedAction | None = None
    closed_at: str | None = None

    @property
    def active(self) -> bool:
        return self.status in ("open", "in_progress")


@dataclass
class Bypass:
    bypass_id: str
    alarm_id: str
    scope: str
    granted_at: str
    granted_by: str
    expire_at: str
    lifted_at: str | None = None
    reason: str = ""

    def state_at(self, at: datetime) -> str:
        """active / expired / lifted。"""
        if self.lifted_at:
            return "lifted"
        return "active" if at < parse_iso(self.expire_at) else "expired"


# ---------- 召回 ----------

@dataclass
class Recall:
    recall_id: str
    part_no: str
    batches: list[str]
    reason: str
    issued_at: str
    withdrawn_at: str | None = None

    def matches(self, part_no: str, batch: str) -> bool:
        return self.part_no == part_no and batch in self.batches and not self.withdrawn_at


# ---------- 放行 ----------

@dataclass
class ReleaseRecord:
    release_id: str
    alarm_id: str
    machine_id: str
    wo_id: str
    at: str
    reviewer: str
    reviewer_cert_id: str
    software_version: str
    params_version: str
    resumed_caps: list[str]
    rationale: str
    gate_checks: dict
    actions: list[dict]


# ---------- 报警案卷 ----------

@dataclass
class AlarmCase:
    alarm_id: str
    machine_id: str
    raised_at: str
    raw: dict                            # 报警原文（code/message/source/severity…）
    state: str = V.ALARM_OPEN
    cause: str | None = None
    cause_confidence: str | None = None
    triaged_at: str | None = None
    triaged_by: str | None = None
    sessions: list[DiagSession] = field(default_factory=list)
    work_orders: list[WorkOrder] = field(default_factory=list)
    release: ReleaseRecord | None = None
    rejected_reason: str | None = None

    @property
    def active_work_order(self) -> WorkOrder | None:
        for wo in reversed(self.work_orders):
            if wo.active:
                return wo
        return None


# ---------- 停机窗口 ----------

@dataclass
class Window:
    window_id: str
    machine_id: str
    start_at: str
    end_at: str
    kind: str                 # scheduled / berth_negotiated / emergency
    note: str = ""
    cancelled: bool = False

    def covers(self, at: datetime) -> bool:
        return (not self.cancelled
                and parse_iso(self.start_at) <= at < parse_iso(self.end_at))


# ---------- 设备 ----------

@dataclass
class Machine:
    machine_id: str
    name: str
    model: str
    registered_at: str
    config: dict = field(default_factory=dict)
    windows: list[Window] = field(default_factory=list)
    components: dict[str, ComponentInstall] = field(default_factory=dict)  # slot -> 当前在装
    component_history: list[ComponentInstall] = field(default_factory=list)
    software: dict[str, SoftwareState] = field(default_factory=dict)       # controller -> 状态
    calibrations: list[CalibrationRecord] = field(default_factory=list)
    alarms: dict[str, AlarmCase] = field(default_factory=dict)
    bypasses: list[Bypass] = field(default_factory=list)
    state: str = V.ST_RUNNING

    def active_alarms(self) -> list[AlarmCase]:
        return [a for a in self.alarms.values()
                if a.state not in (V.ALARM_RELEASED,)]

    def latest_calibration(self, slot: str) -> CalibrationRecord | None:
        hits = [c for c in self.calibrations if c.slot == slot]
        return hits[-1] if hits else None
