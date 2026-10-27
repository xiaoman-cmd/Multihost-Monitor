# Phase 04 API

沿用 /api/auth/login、/me、/logout。所有以下接口要求管理员 HttpOnly 会话；POST/PUT 另外要求精确 Origin 与 X-CSRF-Token。无 Agent 新接口，无跨主机写入例外。API Cache-Control: no-store。时间均 UTC Unix 秒。409 为名称／身份冲突，422 为范围／配置无效，503 为 SQLite 暂忙，429 为测试通知冷却。错误不回传 SQL、目标密钥或网络异常原文。

| 方法 | 路径 | 用途与限制 |
|---|---|---|
| GET | /api/v1/checks | after_id 升序 keyset，limit 1–100（默认 50）；items、next_after_id、server_time；latest 仅当前 revision |
| POST | /api/v1/checks | 创建，上限 500 条；201 |
| PUT | /api/v1/checks/{id} | 完整更新，host_id 不可改；revision++，立即进入待调度；旧配置在途结果丢弃 |
| GET | /api/v1/checks/{id}/results | before_id 降序 keyset，since/until UTC，最近 7 天，limit≤100；check、items、next_before_id；记录含 check_revision、http_status、tls_expires_at |
| GET / POST | /api/v1/alert-rules | 读取全部≤200 条／创建；201 |
| PUT | /api/v1/alert-rules/{id} | 完整更新；评估过的规则不能改变 host/metric/target（409，需新建）；重置 Pending 计时 |
| GET | /api/v1/alerts | state=all/normal/pending/firing/resolved，before_id，limit≤100；items、next_before_id、server_time |
| POST | /api/v1/alerts/{id}/ack | 仅 Firing 可确认；幂等，不重复创建确认事件；抑制本生命周期重复通知 |
| POST | /api/v1/alerts/{id}/silence | {seconds:0..604800}；0 解除；持久化期限并创建管理审计事件 |
| GET | /api/v1/alert-events | 可按 alert_id 筛选；before_id、limit≤100；kind=firing/resolved/acknowledged，message 区分 repeat、确认与静默 |
| GET / POST | /api/v1/notification-channels | 读取≤50 个／创建；配置仅 name、kind=webhook/email、secret_ref、enabled |
| PUT | /api/v1/notification-channels/{id} | 完整更新，永不接受实际 Token／SMTP 密码 |
| POST | /api/v1/notification-channels/{id}/test | 202 {delivery_id,status:pending}；渠道需启用，每渠道 60 秒一次；明确用户操作才入 Outbox |
| GET | /api/v1/notification-deliveries | before_id、limit≤100；结果不含 payload 或 secrets；status=pending/sent/failed、attempts、时间、last_error、幂等键 |

## 检查输入

host_id（已存在）、name（1–128 非空字符）、kind=http/tcp、target（≤2048）。HTTP target 为 http(s)://host[:port]/path，TCP 为 tcp://host:port；不允许 userinfo、fragment、控制字符。创建时只验证语法，任务解析 DNS 后执行地址策略；被策略拒绝显示 unknown，不会发起请求。禁止自动重定向。

interval_seconds 默认 60、范围 30–3600；timeout_seconds 默认 5、范围 1–15 且不超过周期。HTTP expected_status_min/max 默认 200/299，范围 100–599 且上限不小于下限；tls_min_days 默认 14、范围 0–365；enabled 默认 true。证书校验失败为 down；可信证书距离到期不足阈值也为 down（certificate_expiring）。HTTP 不读取响应正文。

## 规则输入

name 唯一；host_id 可空（应用于所有主机）。metric 复用 cpu_percent、memory_percent、disk_percent、service_down、host_offline；memory_percent 是**可用内存比例**，不是主机图表的已用比例。

operator=gt/gte/lt/lte；threshold 与 recovery_threshold 为有限 0–100 数值；上阈值规则恢复值须严格更低，下阈值须严格更高，防止边界振荡。二值服务／离线规则固定 gte 1、恢复 0。duration_seconds 默认 60（0–86400），cooldown_seconds 默认 3600（60–604800）；severity=info/warning/critical；enabled；channel_ids≤16 个唯一有效渠道 ID。

target：CPU、内存、离线须空；磁盘为空取已上报分区最大使用率，或指定挂载点（≤128 字符）；systemd 为主机白名单 .service，或 check:<id>（必须绑定该检查的 host_id）。指定目标缺失／不可读、未知服务状态、过期样本不会视作正常。

state 是持久生命周期字段的派生值；data_status=present/missing 独立返回。Normal 只意味着该实例未触发过，须结合 data_status 判断。last_value 缺失为 null。禁用规则／主机暂停判断、重置 Pending，不能强行生成 Resolved。确认不阻止真实恢复通知；静默期间事件保留但不入通知队列，解除不补发。

## 渠道与投递

secret_ref 仅 `[A-Za-z0-9_-]{1,80}.json`；服务用户维护本地 0700 目录与 0600 单链接普通文件，禁止符号链接。Webhook URL、Bearer Token 和 SMTP 凭据均在文件中，API 不能读取内容。Webhook 仅 HTTPS、独立通知 CIDR 策略；SMTP 也受该策略，强制隐式 TLS 或 STARTTLS。实际 secret 文件结构与权限流程见 README。

Outbox 和事件同事务。领取后 30 秒租约；失败指数退避 5 秒起、上限 900 秒、另加 0–4 秒抖动，最多 5 次领取尝试（崩溃也消耗尝试），到期租约可重领。幂等键在持久化后重试不变；HTTP Idempotency-Key、邮件 Message-ID 稳定。外部接收方需配合去重，SMTP 无端到端 exactly-once 承诺。禁用渠道停止新的领取；已开始的投递可能完成。不提供任意 Shell、远程探测脚本或重试目标 URL 覆盖接口。
