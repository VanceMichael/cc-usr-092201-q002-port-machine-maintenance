"""领域词汇：事件类型、状态、枚举常量与中文标签。

事件名用英文代码（写入日志稳定），展示层使用中文标签；标签与
fixtures/domain.json 中的流程状态保持一致。
"""

from __future__ import annotations

# ---------- 事件类型 ----------

# 基础登记
MACHINE_REGISTERED = "machine_registered"
PERSON_REGISTERED = "person_registered"
WINDOW_DEFINED = "window_defined"
PLAN_PUBLISHED = "plan_published"
STOCK_RECEIVED = "stock_received"
COMPONENT_FITTED = "component_fitted"
SOFTWARE_VERSION_SET = "software_version_set"
PARAMS_DEPLOYED = "params_deployed"
CALIBRATION_RECORDED = "calibration_recorded"

# 报警与诊断
ALARM_RAISED = "alarm_raised"
TRIAGE_CLASSIFIED = "triage_classified"
DIAG_SESSION_OPENED = "diag_session_opened"
DIAG_DATA_ACCESSED = "diag_data_accessed"
DIAG_SESSION_CLOSED = "diag_session_closed"

# 检修
WORK_ORDER_OPENED = "work_order_opened"
WORK_ORDER_CANCELLED = "work_order_cancelled"
PART_RESERVED = "part_reserved"
PART_REPLACED = "part_replaced"
PARAMS_ROLLED_BACK = "params_rolled_back"
BYPASS_GRANTED = "bypass_granted"
BYPASS_LIFTED = "bypass_lifted"
RETEST_PERFORMED = "retest_performed"
WORK_ORDER_CLOSED = "work_order_closed"

# 召回与放行
RECALL_ISSUED = "recall_issued"
RELEASE_APPROVED = "release_approved"
RELEASE_REJECTED = "release_rejected"

EVENT_TYPES = {
    MACHINE_REGISTERED, PERSON_REGISTERED, WINDOW_DEFINED, PLAN_PUBLISHED, STOCK_RECEIVED,
    COMPONENT_FITTED, SOFTWARE_VERSION_SET, PARAMS_DEPLOYED,
    CALIBRATION_RECORDED, ALARM_RAISED, TRIAGE_CLASSIFIED,
    DIAG_SESSION_OPENED, DIAG_DATA_ACCESSED, DIAG_SESSION_CLOSED,
    WORK_ORDER_OPENED, WORK_ORDER_CANCELLED, PART_RESERVED,
    PART_REPLACED, PARAMS_ROLLED_BACK, BYPASS_GRANTED, BYPASS_LIFTED,
    RETEST_PERFORMED, WORK_ORDER_CLOSED, RECALL_ISSUED,
    RELEASE_APPROVED, RELEASE_REJECTED,
}

# ---------- 设备/事件流程状态（与 fixture 一致） ----------

ST_RUNNING = "运行中"
ST_ALARMED = "已告警"
ST_DIAGNOSING = "诊断中"
ST_MAINTENANCE = "检修中"
ST_PENDING_RETEST = "待复测"
ST_RELEASED = "已放行"

MACHINE_STATES = [
    ST_RUNNING, ST_ALARMED, ST_DIAGNOSING, ST_MAINTENANCE,
    ST_PENDING_RETEST, ST_RELEASED,
]

# 报警工单生命周期
ALARM_OPEN = "open"
ALARM_TRIAGED = "triaged"
ALARM_IN_REPAIR = "in_repair"
ALARM_PENDING_RETEST = "pending_retest"
ALARM_RELEASED = "released"
ALARM_REJECTED = "release_rejected"

# ---------- 分诊成因（维护主管需要先区分的三类） ----------

CAUSE_MECHANICAL = "mechanical_fault"        # 机械故障
CAUSE_CALIBRATION = "calibration_drift"      # 校准漂移
CAUSE_UNCONFIRMED_PARAMS = "unconfirmed_params"  # 未经确认的软件参数

CAUSE_LABELS = {
    CAUSE_MECHANICAL: "机械故障",
    CAUSE_CALIBRATION: "校准漂移",
    CAUSE_UNCONFIRMED_PARAMS: "未确认软件参数",
}

# ---------- 能力挂起原因 ----------

SUSP_ALARM = "alarm_open"
SUSP_WORK_ORDER = "active_work_order"
SUSP_BYPASS = "active_bypass"
SUSP_BYPASS_EXPIRED = "bypass_expired"
SUSP_RECALL = "recall_batch_hit"
SUSP_UNCONFIRMED = "unconfirmed_params"
SUSP_RETEST_FAILED = "retest_failed"
SUSP_RETEST_PENDING = "retest_pending"

SUSPENSION_LABELS = {
    SUSP_ALARM: "报警未关闭",
    SUSP_WORK_ORDER: "检修作业占用",
    SUSP_BYPASS: "临时旁路生效中",
    SUSP_BYPASS_EXPIRED: "旁路已超时未解除",
    SUSP_RECALL: "命中召回批次",
    SUSP_UNCONFIRMED: "存在未确认软件参数",
    SUSP_RETEST_FAILED: "复测未通过",
    SUSP_RETEST_PENDING: "尚未完成复测",
}

# ---------- 授权 ----------

ROLE_REMOTE_DRIVER = "remote_driver"
ROLE_MAINTENANCE_SUPERVISOR = "maintenance_supervisor"
ROLE_TECHNICIAN = "technician"
ROLE_REVIEWER = "release_reviewer"
ROLE_SCHEDULER = "scheduler"

ROLE_LABELS = {
    ROLE_REMOTE_DRIVER: "远程司机",
    ROLE_MAINTENANCE_SUPERVISOR: "维护主管",
    ROLE_TECHNICIAN: "现场检修",
    ROLE_REVIEWER: "放行复核",
    ROLE_SCHEDULER: "调度席",
}

# 诊断会话可读的数据类别（最小授权）
DATA_MACHINE_CONFIG = "machine_config"
DATA_COMPONENT_HISTORY = "component_history"
DATA_SOFTWARE_PARAMS = "software_params"
DATA_CALIBRATION = "calibration"
DATA_ALARM_RAW = "alarm_raw"

DATA_LABELS = {
    DATA_MACHINE_CONFIG: "设备配置",
    DATA_COMPONENT_HISTORY: "部件履历",
    DATA_SOFTWARE_PARAMS: "软件参数",
    DATA_CALIBRATION: "校准记录",
    DATA_ALARM_RAW: "报警原文",
}

# 检修动作（各自独立签署）
ACTION_REPLACE_PART = "replace_part"
ACTION_ROLLBACK_PARAMS = "rollback_params"
ACTION_GRANT_BYPASS = "grant_bypass"
ACTION_RETEST = "retest"
ACTION_CALIBRATE = "calibrate"

ACTION_LABELS = {
    ACTION_REPLACE_PART: "换件",
    ACTION_ROLLBACK_PARAMS: "参数回退",
    ACTION_GRANT_BYPASS: "临时旁路",
    ACTION_RETEST: "复测",
    ACTION_CALIBRATE: "校准",
}

# 旁路允许的操作范围（窄白名单，而非全部放行）
BYPASS_SCOPE_MANUAL = "manual_remote_only"
BYPASS_SCOPE_SLOW_TRAVEL = "slow_travel"

BYPASS_SCOPE_LABELS = {
    BYPASS_SCOPE_MANUAL: "远程手动",
    BYPASS_SCOPE_SLOW_TRAVEL: "低速移机",
}
