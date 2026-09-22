# 领域模型与事件目录

## 核心对象

| 对象 | 关键字段 | 说明 |
| --- | --- | --- |
| 设备 Machine | machine_id、config、windows、components、software、calibrations、alarms | 一台港机（如自动场桥 RMG-03） |
| 停机窗口 Window | start_at/end_at、kind（计划/靠泊协调/紧急）、cancelled | 检修动作默认必须落在窗口内；紧急方案可豁免 |
| 部件安装 ComponentInstall | slot（安装位）、serial、part_no、**batch**、fitted_at/by | 序列号+批次是召回联动的锚点；更换保留完整履历 |
| 备件 StockItem | stock_id、part_no、serial、batch、status（available/reserved/installed/scrapped） | 预留即锁定给指定案卷，取消工单自动释放 |
| 软件状态 SoftwareState | controller、version、params_version、previous_params_version、confirmed | 参数未经确认（如夜间推送）即构成独立挂起原因，且可回退 |
| 校准 CalibrationRecord | slot、serial、result（pass/drift）、readings、standard（基准器具） | 换件后必须有合格校准才能放行 |
| 人员 Person / 凭证 Cert | role、scope、valid_from/to、allowed_tools、actions | 资质在**动作发生时刻**必须有效；工具必须在凭证清单内 |
| 报警案卷 AlarmCase | raw（报警原文）、cause、sessions、work_orders、release | 所有围绕一条报警的活动挂在同一案卷 |
| 诊断会话 DiagSession | purpose、data_scopes、accesses、TTL | 最小授权容器，每次取用数据单独记一条访问事件 |
| 工单 WorkOrder | plan_id、**locks**、actions、reserved_parts、bypass_id、retest | 资源锁载体；一个案卷同时只有一个活动工单 |
| 签署动作 SignedAction | kind、at、by、tool_id、cert_id、detail | 换件/回退/旁路/校准/复测各自一条，互不合并 |
| 旁路 Bypass | scope（窄白名单）、expire_at、lifted_at | 生效中仅授予白名单能力；超时未解除升级为硬挂起 |
| 召回 Recall | part_no + batches | 在装件命中→能力暂停；备件命中→禁止预留装机 |
| 放行结论 ReleaseRecord | reviewer、软件版本、resumed_caps、rationale、gate_checks、actions | 最终溯源凭证 |

## 设备能力（调度语义）

自动能力：`auto_hoist`（自动起升）、`auto_trolley`（自动小车）、
`auto_spreader`（自动吊具锁止）、`auto_travel`（自动行走）。
旁路窄白名单：`remote_manual`（远程手动）、`slow_travel`（低速移机）。

任何挂起原因存在时自动能力全部暂停；生效旁路只额外授予其白名单能力，
**不会**恢复自动能力。

## 事件目录（日志 type）

基础登记：`machine_registered`、`person_registered`、`window_defined`、
`plan_published`、`stock_received`、`component_fitted`、
`software_version_set`、`params_deployed`、`calibration_recorded`

报警与诊断：`alarm_raised`、`triage_classified`、`diag_session_opened`、
`diag_data_accessed`、`diag_session_closed`

检修：`work_order_opened/cancelled`、`part_reserved`、`part_replaced`、
`params_rolled_back`、`bypass_granted/lifted`、`retest_performed`、
`work_order_closed`

召回与放行：`recall_issued`、`release_approved`、`release_rejected`

事件代码写入日志（稳定），中文标签集中在 `pmms/domain/vocab.py`。
流程状态与 `fixtures/domain.json` 保持一致：
运行中 → 已告警 → 诊断中 → 检修中 → 待复测 → 已放行。
