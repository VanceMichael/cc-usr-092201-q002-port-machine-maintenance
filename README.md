# 自动化港机维护授权

面向高度自动化港区的**维护与能力放行产品**：从报警原文出发，串起设备配置、
部件序列号批次、控制软件与参数、校准记录、检修方案、备件、人员资质和停机窗口；
远程诊断在授权会话内按最小必要取数；换件、参数回退、临时旁路与复测各自签署；
并行检修靠资源锁互不覆盖；离线补录保留实际发生时间；召回批次命中即暂停能力；
调度席拿到的是预计恢复时间与仍可承担的动作，而非笼统的"维修中"。

纯 Python 3.11 标准库实现，可独立运行；所有业务事实落在一条**签名哈希链
事件日志**上，读模型均可重算，最终放行可完整溯源。

## 业务事实

- 智慧港口依赖自动化起重机和智能车辆
- 多数设备由远程操控室运行
- 设备故障会直接影响港口作业连续性
- 一条吊具传感器异常同时牵动远程司机、现场检修与靠泊船期，
  维护主管必须先区分机械故障、校准漂移与未经确认的软件参数

## 快速体验

```bash
python -m pmms.demo                 # 端到端业务故事（自动场桥 RMG-03）
python -m pmms.cli status           # 调度视图：挂起原因 / 可承担动作 / ETR
python -m pmms.cli trace AL-20260922-018
python -m pmms.cli verify
```

## 文档

- [docs/README.md](docs/README.md) — 产品概览与代码结构
- [docs/domain-model.md](docs/domain-model.md) — 领域对象与事件目录
- [docs/workflow.md](docs/workflow.md) — 状态流转与业务规则
- [docs/release-gate.md](docs/release-gate.md) — 放行闸门十项检查
- [docs/eventlog.md](docs/eventlog.md) — 事件日志、签名与完整性
- [docs/api-guide.md](docs/api-guide.md) — API 使用指南

## 项目布局

```
pmms/                         产品实现
  core/      时间、密钥环、签名哈希链日志、错误类型
  domain/    词汇、模型、投影、能力/ETR 读模型
  services/  用例服务、授权会话、数据切片、放行闸门
  demo.py    吊具传感器异常端到端故事
  cli.py     命令行
tests/        52 个单元/场景测试
fixtures/     公开领域资料样例（与产品记录类型、流程状态一致）
contracts/    领域资料 JSON Schema
domain_context/  领域资料加载与基础校验
```

## 测试

```bash
python -m unittest discover -t . -s tests
```

样例使用虚构标识，不含真实个人资料、账号或连接凭据；`data/` 为本地运行
生成的事件库与密钥环（已在 `.gitignore` 中忽略）。
