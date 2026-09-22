# 事件日志与完整性

系统唯一可写存储是仅追加的 JSONL 哈希链日志（`data/events.jsonl`）。
所有读模型（设备状态、案卷、能力、ETR、放行记录）都由日志重算，
不存在第二条"结论库"。

## 事件信封

```json
{
  "seq": 17,
  "type": "part_replaced",
  "occurred_at": "2026-09-22T09:12:00Z",
  "recorded_at": "2026-09-22T09:40:00Z",
  "actor": "TC-01",
  "data": { "...": "业务载荷" },
  "prev_hash": "上一事件哈希（创世为 64 个 0）",
  "hash": "sha256(规范化的除 hash/signature 外全部字段)",
  "signature": { "by": "TC-01", "kid": "k1", "alg": "HS256", "sig": "HMAC" }
}
```

- 规范序列化：键排序、无空白、UTF-8（`pmms.core.signing.canonical`），
  使"签了什么"在任何语言/进程中无歧义。
- 签名密钥每主体独立，存放于 `data/keyring.json`（权限 600）；
  接口与 KMS/SE 模块实现可互换（`Keyring` 协议）。
- 诊断会话令牌用系统主体 `pmms-system` 的密钥签发，不落业务日志。

## 重放校验（verify）

加载或显式校验时逐条检查：

1. `prev_hash` 与前序事件哈希相接（断链即篡改/丢失）；
2. 事件体 SHA-256 与 `hash` 一致（载荷被改即不符）；
3. HMAC 签名有效（连哈希一起伪造也过不了签名）；
4. `occurred_at <= recorded_at`；
5. `recorded_at` 单调递增（按接收顺序追加）。

任一不满足抛 `IntegrityError`，服务拒绝启动。

## 双时间戳与补录

- 在线操作：`occurred_at == recorded_at`。
- 离线补录：二者不同；业务层限制补录窗口 72 小时、禁止未来时间，
  并仍以实际发生时间执行窗口与资质校验。

## 持久化与独立运行

```bash
python -m pmms.demo [数据目录]   # 生成事件库（默认 data/，重跑自动重建事件文件）
python -m pmms.cli verify        # 新进程仅依赖落盘密钥环即可完成验签
```

生产化替换点：`Keyring` → KMS/SE；`EventLog(path)` → 服务端只追加存储；
`SystemClock` → 可信时间源。领域规则与投影层不随存储替换而改变。
