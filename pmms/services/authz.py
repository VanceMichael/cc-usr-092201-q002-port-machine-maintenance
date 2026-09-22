"""诊断会话令牌：授权会话的不可伪造凭证（系统密钥 HMAC）。

载荷含会话、案卷、持有人、数据范围与过期时刻；服务端每次取用数据都
重新验签并检查 TTL，令牌本身不落业务日志。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..core.errors import AuthzError
from ..core.signing import Keyring, canonical
from ..core.time import parse_iso, to_iso

SYSTEM_ID = "pmms-system"


@dataclass(frozen=True)
class SessionToken:
    session_id: str
    alarm_id: str
    by: str
    scopes: tuple[str, ...]
    expire_at: str

    def to_claim(self) -> dict:
        return {
            "session_id": self.session_id,
            "alarm_id": self.alarm_id,
            "by": self.by,
            "scopes": list(self.scopes),
            "exp": self.expire_at,
        }


def issue_token(keyring: Keyring, t: SessionToken) -> str:
    env = keyring.sign(SYSTEM_ID, t.to_claim())
    return env["sig"] + "." + __import__("base64").urlsafe_b64encode(
        canonical(t.to_claim())).decode("ascii").rstrip("=")


def verify_token(keyring: Keyring, token: str, now_iso: str) -> SessionToken:
    import base64
    import binascii

    try:
        sig, claim_b64 = token.split(".", 1)
        pad = "=" * (-len(claim_b64) % 4)
        claim = json.loads(base64.urlsafe_b64decode(claim_b64 + pad))
    except (ValueError, KeyError, binascii.Error, json.JSONDecodeError):
        raise AuthzError("会话令牌格式错误")
    envelope = {"by": SYSTEM_ID, "kid": "k1", "alg": "HS256", "sig": sig}
    try:
        valid = keyring.verify(envelope, claim)
    except Exception:
        valid = False
    if not valid:
        raise AuthzError("会话令牌签名无效")
    if parse_iso(claim["exp"]) < parse_iso(now_iso):
        raise AuthzError("诊断会话已过期，请重新申请授权")
    return SessionToken(
        session_id=claim["session_id"], alarm_id=claim["alarm_id"],
        by=claim["by"], scopes=tuple(claim["scopes"]), expire_at=claim["exp"],
    )
