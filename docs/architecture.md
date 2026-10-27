# Phase 01 架构与接口

## 运行边界

一个 FastAPI/Uvicorn 进程承担 JSON API 与 Vite 构建后的静态页面。SQLite 为本地文件，每条连接设置 foreign_keys=ON、busy_timeout=5000、journal_mode=WAL。SQLAlchemy 同步 Session 按请求建立并关闭，写入使用事务。WAL 允许读写并发，但仍只有一个写者；本阶段不包含后台任务、Agent、调度、告警计算或通知发送。

## 数据约定

主键为 SQLite INTEGER，自增由 SQLite rowid 机制提供；时间统一存 UTC Unix 秒，字节为整数。监控数据按主机、采样时间唯一，磁盘再按挂载点区分。业务状态以字符串保存，通过命名 CHECK 约束限制。SQLAlchemy 必填字段生成 NOT NULL，业务唯一性和外键均由数据库强制执行。默认值主要由 ORM 在写入时赋予，原始 SQL 导入需提供必填值。

| 表 | 用途及约束 |
|---|---|
| hosts | 主机；name 唯一、非空；pending/online/offline/disabled；last_seen_at 可空 |
| agent_credentials | 主机级凭据；只保存唯一 SHA-256 token_hash；过期、撤销时间；无签发 API |
| host_metrics | CPU 0–100、有效内存字节、非负负载；host_id + sampled_at 唯一 |
| disk_metrics | 非空挂载点、有效容量；host_id + mountpoint + sampled_at 唯一 |
| service_checks | http/tcp/process 定义；主机内名称唯一；有效间隔和超时；无执行器 |
| check_results | up/down/unknown；非负延迟；复合外键保证 check 与 host 相符；采样唯一 |
| alert_rules | 全局或指定主机规则；受限指标、比较运算符、阈值、严重性与持续时间 |
| alert_instances | firing/resolved；恢复时间一致性；部分唯一索引防止同指纹活动告警重复，保留恢复历史 |
| alert_events | firing/resolved/acknowledged 事件记录；关联告警 |
| notification_channels | webhook/email 配置与启用状态；配置 JSON，无读写 API |
| notification_deliveries | 事件 + 渠道唯一；pending/sent/failed；非负次数、发送时间一致性 |
| admin_users | 管理员用户名唯一；Argon2id 密码哈希 |
| admin_sessions | SHA-256 会话令牌摘要；管理员外键、CSRF token、创建和绝对过期时间 |

外键为 CASCADE 删除依赖数据，避免孤儿。告警全局规则的 host_id 可空；告警实例的 host_id 始终必填。未来告警计算器必须校验主机规则匹配、具体服务/磁盘指纹等业务语义。本阶段仅建立约束和数据边界。

## 安全会话

管理员由配置在首次启动时创建，密码至少 12 字符、使用 Argon2id。用户名错误与密码错误返回相同错误，未知用户名也执行密码哈希校验。登录限流为每直接连接地址每分钟 5 次、全局每分钟 100 次（包含成功尝试），存储有界且仅适用于单进程。重启会清空限流窗口。

登录生成 256-bit 随机令牌，只在 HttpOnly / SameSite=Strict / Path=/ Cookie 中返回；数据库仅保存 SHA-256 摘要。Cookie Secure 可配置，生产必须启用 HTTPS 和 Secure。会话默认绝对有效期 8 小时，不滚动续期；每个管理员仅一个活动会话，新登录撤销旧会话，退出删除服务端记录及 Cookie。启动与成功登录清理过期会话。CSRF token 存在服务端会话与页面内存中，不使用 localStorage。所有写接口（包括登录）验证精确 Origin，已登录写操作额外验证 X-CSRF-Token；未来新写接口必须复用这些检查。没有开放跨域 CORS。

响应设置 CSP、禁止嵌入、nosniff、no-referrer；API 禁止缓存。静态资源通过路径解析限制在构建目录，未知 API 不返回 SPA。静态壳不含私密数据，所有数据 API 都需认证（健康检查除外）。

## API

| 方法与路径 | 认证 | 行为 |
|---|---|---|
| GET /api/health/live | 无 | 进程存活，200 |
| GET /api/health/ready | 无 | 数据库可访问、迁移版本及 SQLite PRAGMA 正确，200 / 503；不披露内部异常 |
| POST /api/auth/login | 精确 Origin | JSON username/password；200 用户与 CSRF、设置 Cookie；401/403/422/429 |
| GET /api/auth/me | Cookie | 当前用户与 CSRF；未登录或过期 401 |
| POST /api/auth/logout | Cookie + Origin + CSRF | 撤销会话，204；无效会话 401、CSRF 403 |
| GET /api/hosts | Cookie | 真实主机列表，按 ID 最多返回 100 条；无种子数据 |

Phase 01 不开放主机管理、Agent 注册/上报、指标查询、规则管理或通知 API。前端不显示猜测的健康状态、图表或指标。Vite dev server 可用于独立 UI 开发，但它是可选的第二进程；默认单进程路径只使用构建产物。若使用其他前端 origin，必须相应设置 MHM_ORIGIN，生产仍应同源。

## 后续风险

SQLite 未加密；通知配置 JSON 后续接入密钥前必须增加 secret 引用或加密存储。尚未实现审计日志、密码轮换工具、MFA、持久化限流、数据保留/备份策略、调度与通知重试。管理员密码轮换需后续专用运维命令，不应直接修改 `.env` 期待重置。降级会丢弃数据；上线前建立 SQLite backup API 备份与恢复流程。不可在 NFS 或多 worker 模式下沿用当前运行假设。
