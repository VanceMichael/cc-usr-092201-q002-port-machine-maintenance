"""读取并检查共享领域资料。"""

import json
from pathlib import Path

REQUIRED = {
    "domain",
    "version",
    "sample_id",
    "record_types",
    "workflow_states",
    "facts",
    "sample",
    "signoff_actions",
    "parallel_maintenance",
    "offline_recording",
    "recall_linkage",
    "dispatch_view_fields",
    "release_elements",
}

REQUIRED_SIGNOFF_ACTIONS = {"换件", "参数回退", "临时旁路", "复测"}
REQUIRED_RELEASE_ELEMENTS = 5


def load_domain(path: Path) -> dict:
    """返回字段完整并满足处置链规则的领域资料。"""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not REQUIRED.issubset(value):
        raise ValueError("领域资料缺少必要字段")
    if value["version"] < 1 or len(value["record_types"]) < 3 or len(value["workflow_states"]) < 3:
        raise ValueError("领域资料内容不完整")
    _check_signoff_actions(value)
    _check_offline_recording(value)
    _check_dispatch_view(value)
    _check_release(value)
    return value


def _check_signoff_actions(value: dict) -> None:
    actions = {item["action"] for item in value["signoff_actions"]}
    if not REQUIRED_SIGNOFF_ACTIONS.issubset(actions):
        raise ValueError("换件、参数回退、临时旁路与复测四类签署动作必须齐全")


def _check_offline_recording(value: dict) -> None:
    fields = set(value["offline_recording"]["required_fields"])
    if not {"occurred_at", "recorded_at"}.issubset(fields):
        raise ValueError("离线补录必须保留实际发生时间与系统记录时间")


def _check_dispatch_view(value: dict) -> None:
    fields = set(value["dispatch_view_fields"])
    if not {"预计恢复时间", "仍可承担的动作"}.issubset(fields):
        raise ValueError("调度视图必须包含预计恢复时间与仍可承担的动作")


def _check_release(value: dict) -> None:
    if len(value["release_elements"]) < REQUIRED_RELEASE_ELEMENTS:
        raise ValueError("最终放行要素不完整")
