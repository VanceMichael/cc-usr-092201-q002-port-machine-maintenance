import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pmms.core.errors import IntegrityError
from pmms.core.eventlog import EventLog
from pmms.core.signing import Keyring
from pmms.core.time import FixedClock


class EventLogTest(unittest.TestCase):
    def setUp(self):
        self.kr = Keyring()
        self.kr.issue("alice", "s1")
        self.clk = FixedClock(datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc))
        self.log = EventLog(self.clk, self.kr)

    def test_hash_chain_links_events(self):
        self.log.append("a", {"x": 1}, "alice")
        self.log.append("b", {"x": 2}, "alice")
        self.log.verify()
        e1, e2 = self.log.replay()
        self.assertEqual(e1.prev_hash, "0" * 64)
        self.assertEqual(e2.prev_hash, e1.hash)
        self.assertNotEqual(e1.hash, e2.hash)

    def test_signature_rejects_unknown_actor(self):
        with self.assertRaises(IntegrityError):
            self.log.append("a", {"x": 1}, "mallory")

    def test_backfill_preserves_occurred_time(self):
        ev = self.log.append("a", {"x": 1}, "alice",
                             occurred_at="2026-09-22T07:30:00Z")
        self.assertEqual(ev.occurred_at, "2026-09-22T07:30:00Z")
        self.assertEqual(ev.recorded_at, "2026-09-22T08:00:00Z")

    def test_future_occurred_time_rejected(self):
        with self.assertRaises(IntegrityError):
            self.log.append("a", {"x": 1}, "alice",
                            occurred_at="2026-09-22T09:00:00Z")

    def test_tampering_payload_breaks_chain_on_reload(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "events.jsonl"
            log = EventLog(self.clk, self.kr, path)
            log.append("a", {"x": 1}, "alice")
            log.append("b", {"x": 2}, "alice")
            # 直接改磁盘内容：哈希应不匹配
            text = path.read_text("utf-8")
            path.write_text(text.replace('"x": 1', '"x": 9'), "utf-8")
            with self.assertRaises(IntegrityError):
                EventLog(self.clk, self.kr, path)

    def test_tampering_signature_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "events.jsonl"
            log = EventLog(self.clk, self.kr, path)
            log.append("a", {"x": 1}, "alice")
            text = path.read_text("utf-8")
            # 连同 hash 一起伪造 payload，但签名仍由 alice 旧密钥生成不了
            import json
            row = json.loads(text)
            row["data"] = {"x": 9}
            row["hash"] = "f" * 64
            path.write_text(json.dumps(row) + "\n", "utf-8")
            with self.assertRaises(IntegrityError):
                EventLog(self.clk, self.kr, path)

    def test_reload_with_persisted_keyring_verifies(self):
        from pmms.core.signing import PersistedKeyring

        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            kr1 = PersistedKeyring(base / "keyring.json")
            kr1.issue("alice")
            log1 = EventLog(self.clk, kr1, base / "events.jsonl")
            log1.append("a", {"x": 1}, "alice")
            # 新进程：仅靠落盘密钥环即可验签
            kr2 = PersistedKeyring(base / "keyring.json")
            EventLog(self.clk, kr2, base / "events.jsonl").verify()


if __name__ == "__main__":
    unittest.main()
