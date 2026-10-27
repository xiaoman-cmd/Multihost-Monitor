# Phase 02 API 和边界

API 保留 Phase 01 的认证和 `/api/hosts` 返回结构；新增版本化 API。不开发 Dashboard 图表、告警引擎或通知服务。所有时间为 UTC Unix 秒。

| 方法 | 路径 | 身份 | 结果 |
|---|---|---|---|
| POST | /api/v1/hosts | 管理会话 + Origin + CSRF | 201，主机配置与只显示一次的 agent_token |
| GET | /api/v1/hosts | 管理会话 | offset/limit 分页，默认／最多 100 |
| PUT | /api/v1/hosts/{id} | 管理会话 + Origin + CSRF | 替换名称、初始 hostname、厂商、地区、标签、服务白名单 |
| POST | /api/v1/hosts/{id}/token/rotate | 管理会话 + Origin + CSRF | 200，撤销旧 Token 并签发一次性显示的新 Token |
| POST | /api/v1/hosts/{id}/token/revoke | 管理会话 + Origin + CSRF | 204，撤销全部当前 Token |
| GET | /api/v1/hosts/{id}/metrics | 管理会话 | since/until/limit/before_id 查询完整扩展样本；按入库 ID 倒序，游标稳定 |
| GET | /api/v1/agent/config?host_id={id} | Bearer | 30 秒、最多 32 条、systemd 白名单；读取不更新在线时间 |
| POST | /api/v1/agent/heartbeat | Bearer | 200 accepted；合法时间及元数据才更新在线时间 |
| POST | /api/v1/agent/metrics | Bearer | 200 accepted/duplicates 计数；整批事务写入 |

Agent Token 采用 `mhm_` 前缀和 360-bit CSPRNG 随机值，长度 64，数据库仅存 SHA-256 摘要。过期、撤销、禁用主机、错误 Token、host_id 不匹配均返回相同 401 与 WWW-Authenticate。管理凭据与 Agent Bearer 不能互换；Agent 没有匿名注册能力。中心生产网络必须通过受信 HTTPS，Agent 不支持 HTTP 或 TLS 跳过校验。

metrics 请求是 `{"host_id":1,"samples":[...]}`。完整样本字段见 `server/app/agent_schema.py` 和 `agent/internal/model/model.go`，采用严格类型、拒绝额外字段与 NaN/Infinity。sample_id 为规范小写 UUID v4，时间按整秒唯一，重复同 ID 同内容返回成功，重复 ID 不同内容或同时间不同 ID 返回 409；均不会部分落盘。历史范围最近 7 天，未来容忍最多 120 秒；超出 422，Agent 应丢弃而非无限重试。

限制：所有 /api/v1 请求实际读取字节最多 1 MiB（包括无 Content-Length 的流），读取总时限 15 秒，超时 408，每批 1–32 条；磁盘／接口／systemd 各最多 32 且名称不得重复；字符串、路径、服务名有固定长度／格式白名单；字节计数 0..2^63−1；CPU/inode 百分比 0..100；load 0..1e6；速率 0..1e15；uptime 0..2^40；available≤total、swap_used≤swap_total。服务采集目标还必须属于该主机管理员配置白名单。服务名只允许受限字符并以 .service 结尾，本地白名单是第二道独立限制。

原 host_metrics 保存 CPU、total−available 内存用量及 load1，原 disk_metrics 保存容量。新 agent_samples 在同一事务保存完整验证后的扩展 JSON 和内容摘要，并与 host_metrics 一对一。JSON 结构与具体数值边界由版本化 API 严格验证；数据库另以 UNIQUE、FK、CHECK 强制样本身份和关联约束，不声称 JSON 内所有字段均有 SQL CHECK。

成功事务提交才更新 last_seen_at（服务器接收时刻）；metadata_at 使用样本时刻且只允许更新为更大的值，旧补报不能覆盖新的 hostname/OS/arch/Agent 版本。online 表示最近成功接收过；本阶段尚无离线状态调度器，使用 last_seen_at 判断新鲜度。新样本时间与 Phase 01 采样唯一约束一致，秒级碰撞会返回 409。

重试语义：网络错误、408、429、5xx 重试；Retry-After 秒值最高 300 秒，退避 1–300 秒并加入随机抖动；401/403 为需人工修复的终止错误；其他 4xx 不重试。整个批次 409/422 拒绝时，客户端逐条隔离，保留有效样本并丢弃坏样本。只有 accepted+duplicates 等于提交条数才确认队列。数据库忙/不可用统一 503 + Retry-After:5，无内部错误信息。

中心按 SQLite BEGIN IMMEDIATE 串行化凭据校验、撤销、写入和幂等检查；事务失败回滚原指标、扩展样本和在线时间。无 Redis、队列服务或后台采集任务。当前单进程内存缓冲与流量限制不是分布式限流；生产仍需 TLS 终止层的请求超时／并发／速率限制。
