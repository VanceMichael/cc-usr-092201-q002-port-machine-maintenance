"""应用工厂：一键得到可独立运行的维护服务（内存或 data 目录持久化）。"""

from __future__ import annotations

from pathlib import Path

from .core.eventlog import EventLog
from .core.signing import Keyring, PersistedKeyring
from .core.time import Clock, SystemClock
from .services.authz import SYSTEM_ID
from .services.service import MaintenanceService


def create_service(data_dir: str | Path | None = None,
                   clock: Clock | None = None) -> MaintenanceService:
    clock = clock or SystemClock()
    if data_dir:
        base = Path(data_dir)
        base.mkdir(parents=True, exist_ok=True)
        keyring = PersistedKeyring(base / "keyring.json")
        keyring.issue(SYSTEM_ID)
        log = EventLog(clock, keyring, base / "events.jsonl")
    else:
        keyring = Keyring()
        keyring.issue(SYSTEM_ID)
        log = EventLog(clock, keyring)
    return MaintenanceService(log, keyring, clock)
