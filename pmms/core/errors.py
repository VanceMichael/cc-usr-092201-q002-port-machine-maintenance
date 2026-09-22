"""领域错误：所有可预期的业务拒绝都抛出 MaintenanceError 或其子类。"""


class MaintenanceError(Exception):
    """业务规则被违反时的基类，code 供调用方程序化处理。"""

    code = "maintenance_error"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        if code:
            self.code = code


class AuthzError(MaintenanceError):
    """会话缺失、过期、越权访问数据或执行动作。"""

    code = "authz_denied"


class WorkflowError(MaintenanceError):
    """状态机不允许当前动作（如未复测即放行）。"""

    code = "workflow_invalid"


class ConflictError(MaintenanceError):
    """并行作业资源竞争：工单/设备/部件被冲突作业占用。"""

    code = "conflict"


class QualificationError(MaintenanceError):
    """人员资质缺失或过期，或工具不被允许。"""

    code = "qualification"


class IntegrityError(MaintenanceError):
    """日志哈希链断裂、事件被篡改或离线补录时间不合法。"""

    code = "integrity"


class InventoryError(MaintenanceError):
    """备件库存不足或批次不可用。"""

    code = "inventory"


class ReleaseGateError(MaintenanceError):
    """放行条件未全部满足。"""

    code = "release_gate"
