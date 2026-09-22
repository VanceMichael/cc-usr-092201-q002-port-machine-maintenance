"""仅追加哈希链事件日志（系统唯一事实来源）。

每条事件：
  seq / type / occurred_at（实际发生时间）/ recorded_at（系统接收时间）
  / actor / data / prev_hash / hash / signature

- 在线操作 occurred_at == recorded_at；离线补录二者不同，且 occurred_at
  不得晚于 recorded_at（未来时间由用例层给出更细的业务窗口校验）。
- hash = sha256(canonical({除 hash/signature 外全部字段}))，prev_hash 链接前序。
- 持久化为 JSONL，重放时逐条验链、验签；任何篡改直接 IntegrityError。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping

from .errors import IntegrityError
from .signing import GENESIS, Keyring, canonical, digest
from .time import Clock, SystemClock, parse_iso, to_iso


@dataclass
class Event:
    seq: int
    type: str
    occurred_at: str
    recorded_at: str
    actor: str
    data: dict
    prev_hash: str
    hash: str
    signature: dict

    def to_json(self) -> dict:
        return {
            "seq": self.seq,
            "type": self.type,
            "occurred_at": self.occurred_at,
            "recorded_at": self.recorded_at,
            "actor": self.actor,
            "data": self.data,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
            "signature": self.signature,
        }


def _signing_body(ev: Mapping[str, object]) -> dict:
    return {k: v for k, v in ev.items() if k not in ("hash", "signature")}


class EventLog:
    def __init__(self, clock: Clock | None = None, keyring: Keyring | None = None,
                 path: str | Path | None = None):
        self.clock = clock or SystemClock()
        self.keyring = keyring or Keyring()
        self.path = Path(path) if path else None
        self._events: list[Event] = []
        if self.path and self.path.exists():
            self._load()

    # ---------- 追加 ----------

    def append(self, type_: str, data: dict, actor: str, *,
               occurred_at: str | None = None) -> Event:
        recorded = to_iso(self.clock.now())
        occurred = occurred_at or recorded
        if parse_iso(occurred) > parse_iso(recorded):
            raise IntegrityError("实际发生时间不得晚于接收时间（禁止预填未来事件）")
        seq = len(self._events) + 1
        prev_hash = self._events[-1].hash if self._events else GENESIS
        body = {
            "seq": seq,
            "type": type_,
            "occurred_at": occurred,
            "recorded_at": recorded,
            "actor": actor,
            "data": data,
            "prev_hash": prev_hash,
        }
        signature = self.keyring.sign(actor, body)
        ev = Event(hash=digest(body), signature=signature, **body)
        self._events.append(ev)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(ev.to_json(), ensure_ascii=False) + "\n")
        return ev

    # ---------- 读取 ----------

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self) -> Iterator[Event]:
        return iter(self._events)

    def replay(self) -> list[Event]:
        return list(self._events)

    def events_for(self, machine_id: str) -> list[Event]:
        return [e for e in self._events if e.data.get("machine_id") == machine_id]

    def last(self, type_: str | None = None) -> Event | None:
        for ev in reversed(self._events):
            if type_ is None or ev.type == type_:
                return ev
        return None

    # ---------- 完整性 ----------

    def verify(self) -> None:
        """重放校验：签名、哈希、链接、时间顺序（按接收时间）。"""

        prev_hash = GENESIS
        prev_recorded = None
        for ev in self._events:
            raw = ev.to_json()
            body = _signing_body(raw)
            if ev.prev_hash != prev_hash:
                raise IntegrityError(f"事件 {ev.seq} 哈希链断裂")
            if digest(body) != ev.hash:
                raise IntegrityError(f"事件 {ev.seq} 内容哈希不符（疑似篡改）")
            if not self.keyring.verify(ev.signature, body):
                raise IntegrityError(f"事件 {ev.seq} 签名无效：{ev.actor}")
            if parse_iso(ev.occurred_at) > parse_iso(ev.recorded_at):
                raise IntegrityError(f"事件 {ev.seq} 发生时间晚于接收时间")
            if prev_recorded and parse_iso(ev.recorded_at) < prev_recorded:
                raise IntegrityError(f"事件 {ev.seq} 接收时间乱序")
            prev_hash = ev.hash
            prev_recorded = parse_iso(ev.recorded_at)

    def _load(self) -> None:
        for lineno, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            raw = json.loads(line)
            try:
                ev = Event(**raw)
            except TypeError as exc:
                raise IntegrityError(f"第 {lineno} 行事件结构损坏：{exc}")
            self._events.append(ev)
        self.verify()
