"""时间源与离线补录支持。

服务只依赖 Clock 协议，便于测试注入固定时间；BackfillClock 在离线场景下
允许以"实际发生时间"补录，但记录补录登记时间（接收时间），二者都进入日志。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(ISO_FMT)


def parse_iso(value: str) -> datetime:
    return datetime.strptime(value, ISO_FMT).replace(tzinfo=timezone.utc)


class Clock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True)
class SystemClock:
    def now(self) -> datetime:
        return now_utc()


@dataclass
class FixedClock:
    """测试用固定时钟，可手动拨快。"""

    moment: datetime

    def now(self) -> datetime:
        if self.moment.tzinfo is None:
            return self.moment.replace(tzinfo=timezone.utc)
        return self.moment

    def advance(self, seconds: int) -> None:
        from datetime import timedelta

        self.moment = self.moment + timedelta(seconds=seconds)
