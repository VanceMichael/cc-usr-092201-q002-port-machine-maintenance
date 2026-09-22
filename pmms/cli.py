"""命令行：对 data/ 中的事件库进行查询与校验（只读为主）。

用法：
  python -m pmms.cli init-demo [--dir data]   生成演示事件库
  python -m pmms.cli status [--dir data]      全队调度视图（状态/挂起/ETR/可承担动作）
  python -m pmms.cli machine RMG-03           单设备详情
  python -m pmms.cli alarm AL-20260922-018    报警案卷
  python -m pmms.cli trace AL-20260922-018    放行溯源
  python -m pmms.cli sessions AL-...          诊断会话与数据取用记录
  python -m pmms.cli verify                   哈希链与签名完整性
  python -m pmms.cli events [-n 20]           最近事件
"""

from __future__ import annotations

import argparse
import json
import sys

from .app import create_service
from .demo import build_demo
from .domain import vocab as V
from .domain.models import CAPABILITY_LABELS


def _svc(args):
    svc = create_service(args.dir)
    # 离线事件库：以库内最新接收时间作为"当前"评估时刻
    last = svc.log.last()
    args.at = last.recorded_at if last else None
    return svc


def _caps(caps: list[str]) -> str:
    return "、".join(CAPABILITY_LABELS.get(c, c) for c in caps) or "无"


def cmd_init_demo(args) -> int:
    from pathlib import Path

    log_path = Path(args.dir) / "events.jsonl"
    if log_path.exists() and not args.force:
        print(f"{log_path} 已存在；加 --force 可重建。")
        return 1
    if log_path.exists():
        log_path.unlink()
    build_demo(data_dir=args.dir, verbose=False)
    print(f"演示事件库已生成于 {args.dir}/events.jsonl")
    return 0


def cmd_status(args) -> int:
    svc = _svc(args)
    for st in svc.fleet_status(args.at):
        print(f"[{st.state}] {st.machine_id} {st.name}")
        if st.suspensions:
            for s in st.suspensions:
                print(f"  ! {V.SUSPENSION_LABELS.get(s.code, s.code)}：{s.detail}")
        else:
            print("  ! 无挂起")
        print(f"  可承担：{_caps(st.available_caps)}")
        print(f"  ETR：{st.etr or '待定'}（{st.etr_basis}）")
    return 0


def cmd_machine(args) -> int:
    svc = _svc(args)
    m = svc.reg.machines.get(args.machine_id)
    if not m:
        print(f"未知设备：{args.machine_id}")
        return 1
    print(f"{m.machine_id} {m.name}（{m.model}） 状态：{m.state}")
    print("配置：" + json.dumps(m.config, ensure_ascii=False))
    print("在装部件：")
    for slot, c in m.components.items():
        print(f"  {slot} → {c.part_no}/{c.serial} 批次{c.batch}（{c.fitted_at}）")
    print("软件：")
    for s in m.software.values():
        flag = "已确认" if s.confirmed else "未确认"
        print(f"  {s.controller} {s.version} 参数集 {s.params_version}（{flag}）")
    print("校准：")
    for c in m.calibrations:
        print(f"  {c.at} {c.slot}/{c.serial} {c.result} {c.readings}")
    st = svc.machine_status(m.machine_id, args.at)
    print(f"可承担：{_caps(st.available_caps)}　ETR：{st.etr or '待定'}（{st.etr_basis}）")
    return 0


def cmd_alarm(args) -> int:
    svc = _svc(args)
    case = svc._case(args.alarm_id)
    print(f"报警 {case.alarm_id}（{case.machine_id}）状态：{case.state}")
    print(f"原文：{json.dumps(case.raw, ensure_ascii=False)}")
    if case.cause:
        print(f"分诊：{V.CAUSE_LABELS[case.cause]}（{case.cause_confidence}）"
              f" {case.triaged_at} {case.triaged_by}")
    for s in case.sessions:
        print(f"会话 {s.session_id} {s.opened_at} {s.opened_by}"
              f"{' 已关闭@' + s.closed_at if s.closed_at else ' 进行中'}")
        print("  授权范围：" + "、".join(V.DATA_LABELS[x] for x in s.data_scopes))
        for a in s.accesses:
            print(f"    - {a.at} 取用 {V.DATA_LABELS.get(a.scope, a.scope)}：{a.purpose}")
    for wo in case.work_orders:
        print(f"工单 {wo.wo_id} 方案 {wo.plan_id} 状态 {wo.status} "
              f"预估 {wo.estimated_minutes}min 锁 {'、'.join(wo.locks)}")
        for a in wo.actions:
            print(f"  - {a.at} {V.ACTION_LABELS.get(a.kind, a.kind)} "
                  f"by {a.by} 工具 {a.tool_id} 凭证 {a.cert_id} {a.detail}")
    if case.release:
        print(f"放行：{case.release.release_id} {case.release.at} "
              f"复核 {case.release.reviewer}")
    elif case.rejected_reason:
        print(f"放行被拒：{case.rejected_reason}")
    return 0


def cmd_trace(args) -> int:
    svc = _svc(args)
    t = svc.release_trace(args.alarm_id)
    print(f"放行单 {t['release_id']}　{t['machine']['name']}　{t['released_at']}")
    print(f"复核：{t['reviewer']['name']}（{t['reviewer']['cert_id']}）")
    print("软件：" + "；".join(f"{s['controller']} {s['version']}/参数{s['params_version']}"
                              for s in t["software"]))
    print("在装部件：" + "、".join(f"{c['slot']}={c['serial']}批次{c['batch']}"
                                 for c in t["components_after"]))
    print("动作签署链：")
    for a in t["actions"]:
        print(f"  {a['at']} {V.ACTION_LABELS.get(a['kind'], a['kind'])} "
              f"{a['by']} 工具{a['tool_id']} 凭证{a['cert_id']}")
    print(f"恢复：{_caps(t['resumed_caps'])}")
    print(f"复核理由：{t['review_rationale']}")
    print(json.dumps({"gate_checks": t["gate_checks"]}, ensure_ascii=False, indent=2))
    return 0


def cmd_sessions(args) -> int:
    svc = _svc(args)
    case = svc._case(args.alarm_id)
    for s in case.sessions:
        print(f"会话 {s.session_id}　{s.opened_at}　{s.opened_by}"
              f"　{'已关闭 ' + s.closed_at if s.closed_at else '进行中'}")
        print(f"  目的：{s.purpose}")
        print("  授权范围：" + "、".join(V.DATA_LABELS.get(x, x)
                                        for x in s.data_scopes))
        for a in s.accesses:
            print(f"    {a.at} 取用 {V.DATA_LABELS.get(a.scope, a.scope)}：{a.purpose}")
    return 0


def cmd_verify(args) -> int:
    svc = _svc(args)
    print(svc.verify_integrity())
    return 0


def cmd_events(args) -> int:
    svc = _svc(args)
    events = svc.log.replay()[-args.n:]
    for ev in events:
        backfill = " 补录" if ev.occurred_at != ev.recorded_at else ""
        print(f"{ev.seq:>3} {ev.occurred_at}→{ev.recorded_at}{backfill} "
              f"{ev.type} by {ev.actor}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pmms", description="港机维护与能力放行")
    parser.add_argument("--dir", default="data", help="事件库目录")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init-demo"); p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init_demo)
    sub.add_parser("status").set_defaults(func=cmd_status)
    p = sub.add_parser("machine"); p.add_argument("machine_id")
    p.set_defaults(func=cmd_machine)
    p = sub.add_parser("alarm"); p.add_argument("alarm_id")
    p.set_defaults(func=cmd_alarm)
    p = sub.add_parser("trace"); p.add_argument("alarm_id")
    p.set_defaults(func=cmd_trace)
    p = sub.add_parser("sessions"); p.add_argument("alarm_id")
    p.set_defaults(func=cmd_sessions)
    sub.add_parser("verify").set_defaults(func=cmd_verify)
    p = sub.add_parser("events"); p.add_argument("-n", type=int, default=20)
    p.set_defaults(func=cmd_events)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
