# API 使用指南

所有用例从 `MaintenanceService` 进入；业务拒绝抛 `pmms.core.errors` 中的
类型化异常（`AuthzError / WorkflowError / ConflictError /
QualificationError / InventoryError / ReleaseGateError / IntegrityError`）。

```python
from pmms.app import create_service

svc = create_service("data")            # 持久化；create_service() 为内存模式
```

## 1. 基线登记

```python
svc.register_machine("RMG-03", "自动场桥RMG-03", "RMG-AUTO-41t",
                     {"控制器": "spreader-ctrl"})
svc.define_window("RMG-03", "win-am", "2026-09-22T08:00:00Z",
                  "2026-09-22T12:00:00Z", kind="berth_negotiated")
svc.register_person("TC-01", "韩铎", "technician", [{
    "cert_id": "C-TC", "scope": "component:height_sensor",
    "valid_from": "2026-01-01T00:00:00Z", "valid_to": "2026-12-31T23:59:59Z",
    "allowed_tools": ["TL-TQ", "TL-CAL"],
    "actions": ["replace_part", "calibrate", "retest"],
}])
svc.publish_plan("plan-mech", "更换传感器", "mechanical_fault",
                 required_actions=["replace_part", "calibrate", "retest"],
                 required_slots=["spreader.height_sensor"],
                 required_part_no="P-HS-770", estimated_minutes=45)
svc.receive_stock("ST-NEW", "P-HS-770", "SN-NEW-9002", "B2026-03")
svc.set_software("RMG-03", "spreader-ctrl", "ctrl-4.8.2", "p-1108", "SV-01")
```

## 2. 报警 → 分诊 → 授权取证

```python
svc.raise_alarm("RMG-03", "AL-1", {
    "code": "SPREADER_HEIGHT_DEVIATION",
    "message": "吊具高度反馈偏差 38mm 超过阈值 25mm",
    "source": "PLC-SP-03", "severity": "high"})

sid, token = svc.open_diag_session(
    "AL-1", "SV-01", purpose="区分机械/校准/参数成因",
    data_scopes=["alarm_raw", "software_params", "calibration",
                 "component_history", "machine_config"], ttl_minutes=30)
svc.access_diagnostic_data(token, "calibration", "查看漂移趋势")  # 逐次留痕
svc.close_diag_session(sid, "SV-01")

svc.triage("AL-1", "SV-01", "mechanical_fault", "high",
           rationale="参数未变、校准持续漂移且部件超期")
```

## 3. 检修：工单、签署动作、离线补录

```python
svc.open_work_order("AL-1", "plan-mech", "SV-01")
svc.reserve_part("AL-1", "ST-NEW", "SV-01")
# 离线补录：传 occurred_at 保留实际发生时间（recorded_at 自动为当前时间）
svc.replace_part("AL-1", "ST-NEW", "spreader.height_sensor",
                 "TC-01", "C-TC", "TL-TQ",
                 occurred_at="2026-09-22T09:12:00Z")
svc.record_calibration("RMG-03", "spreader.height_sensor",
                       "TC-01", "C-TC", "pass", {"deviation_mm": 1.1},
                       "STD-LASER", alarm_id="AL-1",
                       wo_id="wo-AL-1-01", tool_id="TL-CAL")
svc.retest("AL-1", "pass", {"联锁动作": 10}, "STD-LASER",
           "TC-01", "C-TC", "TL-CAL", notes="10 次全通过")
```

旁路与参数回退：

```python
svc.grant_bypass("AL-1", "manual_remote_only", "SV-01", "C-SUP", "TL-AUTH",
                 reason="配合移舱", ttl_minutes=20)
svc.lift_bypass("bp-AL-1-01", "SV-01")
svc.rollback_params("AL-1", "spreader-ctrl", "TC-02", "C-SW", "TL-PRG")
```

## 4. 召回

```python
svc.issue_recall("RC-041", "P-HS-770", ["B2026-03"],
                 reason="温补电路高温零漂")   # 在装命中→能力立即暂停
```

## 5. 放行与溯源

```python
result = svc.request_release("AL-1", "RV-01", "C-RV",
                             rationale="非召回批次、校准合格、复测通过")
trace = svc.release_trace("AL-1")
# trace["software"] / trace["actions"] / trace["gate_checks"]
# trace["review_rationale"] / trace["resumed_caps"]
```

## 6. 调度视图与完整性

```python
for st in svc.fleet_status():
    st.state, st.suspensions, st.available_caps, st.etr, st.etr_basis

svc.verify_integrity()   # {'events': N, 'status': 'ok'}
```

## 测试与可复现时间

测试中注入固定时钟：

```python
from pmms.app import create_service
from pmms.core.time import FixedClock
from datetime import datetime, timezone

clk = FixedClock(datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc))
svc = create_service(clock=clk)
clk.advance(600)   # 拨快 10 分钟
```
