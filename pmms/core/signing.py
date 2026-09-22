"""签名工具：人员/系统动作的 HMAC 签署与密钥环。

真实部署中密钥来自 KMS/SE 模块；此处给出文件化密钥环接口，保证：
- 每个签名人持有独立密钥；
- 支持密钥轮换（同一人多版本 key_id，验签时按历史 key_id 取旧密钥）；
- 签名内容为规范化 JSON（键排序、无空白），使"签了什么"无歧义。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .errors import IntegrityError

GENESIS = "0" * 64


def canonical(payload: object) -> bytes:
    """事件/动作载荷的规范序列化：键排序、分隔符固定、无多余空白。"""

    return json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def digest(payload: object) -> str:
    return hashlib.sha256(canonical(payload)).hexdigest()


def b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64d(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


@dataclass
class Keyring:
    """签名密钥环。key_at 存放 (主体, key_id) -> base64 密钥。"""

    key_at: dict[tuple[str, str], str] = field(default_factory=dict)

    def issue(self, subject: str, secret: str | None = None, key_id: str = "k1") -> None:
        if (subject, key_id) in self.key_at:
            return
        raw = secret.encode("utf-8") if secret else secrets.token_bytes(32)
        self.key_at[(subject, key_id)] = b64e(hashlib.sha256(raw).digest())

    def _raw(self, subject: str, key_id: str) -> bytes:
        try:
            return b64d(self.key_at[(subject, key_id)])
        except KeyError:
            raise IntegrityError(f"未知签名密钥：{subject}/{key_id}")

    def sign(self, subject: str, payload: object, key_id: str = "k1") -> dict:
        mac = hmac.new(self._raw(subject, key_id), canonical(payload), hashlib.sha256)
        return {"by": subject, "kid": key_id, "alg": "HS256", "sig": b64e(mac.digest())}

    def verify(self, envelope: Mapping[str, str], payload: object) -> bool:
        subject = envelope["by"]
        key_id = envelope.get("kid", "k1")
        expected = self.sign(subject, payload, key_id)["sig"]
        return hmac.compare_digest(expected, envelope["sig"])


class PersistedKeyring(Keyring):
    """落盘密钥环：data/keyring.json（权限 600），重启后仍可验签。

    演示/本地独立运行使用：每个主体首次发证时生成随机密钥并持久化；
    生产部署应替换为 KMS 或安全模块支持的同名接口实现。
    """

    def __init__(self, path):
        super().__init__()
        self.path = Path(path)
        if self.path.exists():
            self.key_at = {
                tuple(k.split("|", 1)): v
                for k, v in json.loads(self.path.read_text("utf-8")).items()
            }

    def issue(self, subject: str, secret: str | None = None, key_id: str = "k1") -> None:
        if (subject, key_id) in self.key_at:
            return
        raw = secret.encode("utf-8") if secret else secrets.token_bytes(32)
        self.key_at[(subject, key_id)] = b64e(hashlib.sha256(raw).digest())
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {f"{s}|{k}": v for (s, k), v in self.key_at.items()}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        self.path.chmod(0o600)
