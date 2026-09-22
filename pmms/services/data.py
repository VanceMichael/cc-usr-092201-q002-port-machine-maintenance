"""按授权范围装配诊断数据切片：会话只能读到被批准的数据类别。"""

from __future__ import annotations

from ..domain import vocab as V
from ..domain.models import Machine
from ..domain.projector import Registry


def build_slice(reg: Registry, m: Machine, scope: str) -> dict:
    if scope == V.DATA_ALARM_RAW:
        out = {}
        for alarm_id, case in m.alarms.items():
            out[alarm_id] = {"raised_at": case.raised_at, "raw": case.raw,
                             "state": case.state}
        return {"alarms": out}

    if scope == V.DATA_MACHINE_CONFIG:
        return {"machine_id": m.machine_id, "name": m.name, "model": m.model,
                "config": m.config}

    if scope == V.DATA_COMPONENT_HISTORY:
        return {
            "installed": [
                {"slot": c.slot, "part_no": c.part_no, "serial": c.serial,
                 "batch": c.batch, "fitted_at": c.fitted_at, "fitted_by": c.fitted_by}
                for c in m.components.values()
            ],
            "history": [
                {"slot": c.slot, "part_no": c.part_no, "serial": c.serial,
                 "batch": c.batch, "fitted_at": c.fitted_at,
                 "removed_at": c.removed_at}
                for c in m.component_history
            ],
        }

    if scope == V.DATA_SOFTWARE_PARAMS:
        return {
            "controllers": [
                {"controller": s.controller, "version": s.version,
                 "params_version": s.params_version,
                 "previous_params_version": s.previous_params_version,
                 "deployed_at": s.deployed_at, "deployed_by": s.deployed_by,
                 "confirmed": s.confirmed}
                for s in m.software.values()
            ],
        }

    if scope == V.DATA_CALIBRATION:
        return {
            "calibrations": [
                {"cal_id": c.cal_id, "slot": c.slot, "serial": c.serial,
                 "at": c.at, "by": c.by, "result": c.result,
                 "readings": c.readings, "standard": c.standard}
                for c in m.calibrations
            ],
        }

    raise ValueError(f"未知数据范围：{scope}")
