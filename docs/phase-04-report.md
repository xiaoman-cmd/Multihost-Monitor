# MultiHost Monitor V1.0 — Phase 04 阶段报告

日期：2026-10-27（Asia/Shanghai）。结论：**GO，限 Phase 04 本地开发与测试验收**。没有部署到生产环境，没有向真实第三方发送通知。完成本阶段后停止，等待 Phase 05 部署授权；此结论不替代 Linux 实机、真实邮件服务商或生产容量验收。

## 1. Git 与审计基线

- 开始时分支 `codex/phase03-dashboard`，工作区干净。Phase 03 基线 `d9229319d553d0a9798116f31315b9f769aad66c`，Phase 03 源码 `ddfa464aca7a0bcb617d5c62133456d92d37cd38`；已有阶段报告已读取，未改写旧验收结论。
- 本阶段分支 `codex/phase04-alerts`。工作区出现的实现提交 `ac86e0ec3ea79bb2ebb1427d4f1e03391c50b506` 已保留；输入保护及证据补充提交 `ca3ce6ebab48f3afd1c824bc6af82bcbb60ff008`。
- 最终源码候选：`76bafacc14b5a0d27ea45d9acb374da602beb12d`。本报告独立提交，避免自引用提交 SHA。
- 基线复验：Python 131 项通过；React 11 项及构建通过；Go race 全部包通过。最初受限沙箱阻止 Go 读取主机信息／监听端口，允许本机系统读取与临时回环监听后正常通过，未把权限错误当作跳过。
- 已检查七张既有表的实际迁移 Schema、外键、唯一／检查约束、索引及前端会话。实际 0003 与 0004 Schema 前后对比、迁移哈希与 PRAGMA 保存在 `docs/evidence/phase04/schema-security-audit.json`。
- 0001/0002/0003 逐字节保持不变；Agent 源码、认证／采样协议、pyproject.toml、uv.lock、前端依赖与锁文件未改变。没有引入 Docker、Redis、Celery 或其他运行依赖。

## 2. 实际实现与验收

| 范围 | 实际实现 | 结果 |
|---|---|---|
| HTTP/TCP | HTTP/HTTPS GET、TCP 连接、延迟、状态码范围、TLS 剩余有效期、超时、版本化历史 | 本地协议集成测试通过 |
| 默认周期 | 60 秒、超时 5 秒；配置范围 30–3600 秒／1–15 秒 | API 与 UI 默认值测试通过 |
| 出站安全 | DNS 所有候选先验证，数值 IP 固定连接，SNI／证书使用原域名；禁止重定向／代理；默认拒绝私网等非全球单播地址 | SSRF、混合 DNS、重绑定连接参数与真实 302 测试通过 |
| 特殊目标 | 已知 IPv4/IPv6 云元数据、链路本地、未指定、多播与转换 IPv6 拒绝；私网例外只能由中心保护配置显式指定 | 默认拒绝与不可覆盖元数据测试通过 |
| 资源监控 | 原 Agent CPU、可用内存比、磁盘使用率、systemd 白名单状态，中心 last_seen_at 离线判断 | 指标、缺失、离线／恢复测试通过 |
| 四态 | Normal、Pending、Firing、Resolved，连续异常计时、严格滞回、确认、静默、冷却、恢复 | 状态、并发、冷却和重启重读测试通过 |
| 缺失处理 | 过期／不存在／unknown 数据独立标记 missing；不恢复 Firing；重置 Pending 连续窗口 | 通过；策略拒绝探测为 unknown，不能冒充服务正常／失败 |
| Outbox | 状态、事件和通知记录同一短事务；独立领取、持久租约、指数退避加抖动、5 次上限、稳定幂等键 | 回滚、失败重试、租约恢复和去重测试通过 |
| Webhook/SMTP | HTTPS Webhook，SMTP 隐式 TLS 或 STARTTLS；稳定 Idempotency-Key／Message-ID；中文正文采用七位安全 MIME 编码 | 实际回环 TLS 传输与协议测试通过 |
| 凭据 | 仅受保护外部 JSON 引用；目录 0700、文件属中心用户 0600、普通单链接、禁止符号链接／路径穿越；不接受／返回实际密钥 | API 和文件安全测试通过 |
| 管理 UI | 检查列表／创建／编辑／历史，规则管理，告警筛选与确认／静默，事件，渠道与发送历史；显示本地时区、API UTC | 组件测试及桌面／375px QA 通过 |
| 调度 | lifespan 启停，数据库相邻 flock 防止第二调度器；探测 4 并发／每批 32，投递 2 并发／每批 16；评估每批≤100 对与 2 秒软预算 | 并发、取消、生命周期、独立错误处理通过 |
| 入库隔离 | 网络 I/O 不在 SQLite 写事务中；计算与投递不在 Agent 指标入库事务中 | 投递期间实际 Agent API 成功入库，锁／回滚测试通过 |
| 清理 | 原始指标及检查历史后台分批保留 7 天；告警／通知审计暂不自动删除 | 外键与 WAL 保持正常 |

没有实现 SSH、任意 Shell、远程重启、自动修复、防火墙控制。原 process 类型仅为旧 Schema 兼容保留，本阶段 API 不允许创建／执行 process 检查。

## 3. 0004 增量迁移与复用

新增 `server/migrations/versions/0004_phase04_monitoring.py`，版本链至 `0004_phase04`。

| 既有表 | 复用和增量 |
|---|---|
| service_checks | 复用 host/name/kind/target/周期/超时/enabled；增加成功码范围、TLS 天数、next_run_at、revision |
| check_results | 复用状态／延迟／时间／消息与联合外键；增加 http_status、tls_expires_at、check_revision |
| alert_rules | 复用已有指标、比较、阈值、持续时间与严重级别；增加恢复阈值、冷却、目标、渠道 IDs |
| alert_instances | 保留原 firing/resolved 状态列与约束；新增 pending_since、fired_at、评估与数据状态、确认、静默、通知计时 |
| alert_events | 保留原关系与 kind；firing/resolved 事件记录周期，acknowledged 记录管理操作，message 区分原因／repeat |
| notification_channels | 不新建重复凭据列，config 仅保存 secret_ref；非空旧明文配置在任何 DDL 前拒绝升级 |
| notification_deliveries | 直接作为 Outbox；增加 payload、幂等键、重试时间和租约；测试消息 event_id 可空，真实告警仍受事件外键约束 |

四态由持久化字段唯一派生：status=firing → Firing；非 firing 且 pending_since 存在 → Pending；曾 fired → Resolved；否则 Normal。避免增加一个会与原 status 竞争的冗余状态列。Normal 必须同时查看 data_status，不表示缺失数据健康。

仅 notification_deliveries 无被引用方的叶表需要重建；其余为增量加列／索引。已有事件与投递在 upgrade → downgrade → reupgrade 中保留，`alembic check` 无 drift，FK/WAL/integrity 正常。downgrade 会丢弃新状态字段和 event_id 为空的测试投递，不是无损运维承诺；生产回滚应停写并恢复一致性备份。

## 4. API 与界面

完整契约见 `docs/phase-04-api.md`。

- `/api/v1/checks` GET/POST、`/{id}` PUT、`/{id}/results` GET。
- `/api/v1/alert-rules` GET/POST、`/{id}` PUT。
- `/api/v1/alerts` GET、`/{id}/ack` POST、`/{id}/silence` POST。
- `/api/v1/alert-events` GET。
- `/api/v1/notification-channels` GET/POST、`/{id}` PUT、`/{id}/test` POST。
- `/api/v1/notification-deliveries` GET。

全部读取要求原管理员会话，全部写入要求 Origin + CSRF。新接口的无效请求不会回显拒绝的秘密字段或把 NaN/Infinity 序列化为 500；旧 Agent 错误契约不变。历史分页有固定数量上限；检查结果带配置版本，不能用旧目标结果触发新目标告警。

UI 不写 Token 到 localStorage／URL／日志，不接受 SMTP 密码或 Webhook Token；渠道设置只输入文件引用。确认／静默／测试发送前有说明与确认操作。实际配置仍须管理员安全准备外部文件，这不是生产安装流程。

## 5. 实际测试命令与结果

环境：macOS 13.1 x86_64、Python 3.12.13、Go 1.27.3 darwin/amd64，使用仓库锁定的前端依赖。全部数据库隔离，不写正式监控库。

| 命令 | 实际结果 | 原始证据 |
|---|---|---|
| `.venv/bin/pytest -q` | **214 passed**，90.42 秒；含原 Phase 01–03 131 项与本阶段 83 项 | evidence/phase04/pytest.txt |
| `npm --prefix web test` | **18 passed / 4 files**，19.84 秒；原 11 项 + 新 7 项 | evidence/phase04/web-tests.txt |
| `npm --prefix web run build` | TypeScript 与 Vite 成功，6.05 秒 | evidence/phase04/web-build.txt |
| `go test -race -count=1 ./...`（agent 目录） | 全部包通过，19 个顶层测试；Agent 源码未改 | evidence/phase04/go-race.txt、go-checks.json |
| `go vet ./...`、`staticcheck ./...` | 返回码 0 | evidence/phase04/go-checks.json |
| Alembic API upgrade/check/downgrade/reupgrade | 成功；旧记录保留、无 drift、外键与完整性通过 | schema-security-audit.json、pytest.txt |
| `PYTHONPATH=server .venv/bin/python server/tools/benchmark_phase04.py` | 临时 SQLite 基准完成，无网络发送 | performance.json |
| `PYTHONPATH=server .venv/bin/python server/tools/preview_phase04.py` | 实际回环探测／UI 生命周期完成，已停止清理 | loopback-lifecycle.json、browser.json |

具体工具链路径、执行说明见 `docs/evidence/phase04/commands.json`。Python 有 1 条上游 Starlette TestClient/httpx 弃用警告；Node 有 Fetch API 实验性提示、npm 日志目录写权限提示，退出码仍为 0。未为消除警告更改锁定依赖。

覆盖 HTTP 200/500/超时／失败、TCP 成功／拒绝／超时、可信 TLS 与证书剩余期、重定向、SSRF、全部候选校验与固定 IP；资源、systemd、离线恢复；Pending、缺失、滞回、重复冷却、确认、静默；Outbox 事务回滚、并发去重、租约、重启与尝试上限；实际回环 HTTPS Webhook、TLS/STARTTLS 邮件；管理认证／CSRF、迁移、清理及全部原阶段回归。DNS 重绑定使用解析替身和真实连接参数断言，不声称操作了公共 DNS 服务。详细映射见 `docs/phase-04-test-plan.md`。

## 6. 性能与运行边界

本机**隔离合成库**：50 主机、2 条全局规则，共 100 对评估对象；首次评估创建 100 实例、100 事件、200 条 Outbox。无网络投递、无生产数据，测量时同机也在运行回归任务。

- 首次计算并原子落库：**1073.63 ms**。
- 5 次稳定批次：447.77 / 471.46 / 516.91 / 471.72 / 481.51 ms，中位 **471.72 ms**。
- 领取 16 条 Outbox：**26.55 ms**；FK 错误 0，journal_mode=wal。
- JS **197.32 kB**（gzip 64.87 kB），CSS **27.60 kB**（gzip 6.75 kB）。

配置上限不是时效容量保证：500 检查／200 规则是安全配置硬上限，超时或大量全局规则会出现调度延迟；检查为 best-effort 周期，页面标出过期，评估跨未知窗口不会假定持续异常。生产需在目标机器测量实际规则／主机规模和 Outbox 积压。没有运行 Oracle／Google 容量测试或宣称满足生产吞吐。

## 7. 实际浏览器与故障证据

自有回环 HTTP 测试端点实际返回 200 → 503 → 200，由真实调度器网络请求写入检查历史，再计算告警；没有直接插入检查结果或伪造资源指标。

- 原始 **27 条探测**：22 up、5 down，含实际 HTTP 状态与毫秒延迟。
- 实际事件：Firing UTC 1793103099、管理员确认 UTC 1793103226、Resolved UTC 1793103247。
- 临时库 host_metrics **0 条**、notification_channels **0 个**、notification_deliveries **0 条**；浏览器 QA 没有发送第三方通知。
- 375px 下检查列表、检查／渠道表单 scrollWidth=375，告警和事件页=360（滚动条差异），无全局横向溢出。桌面事件截图 1265px；checks-compact.jpg 为较窄布局，不标作 1280px 桌面证据。
- 浏览器 console warn/error 为 0。预览前端仅本机 HTTP，非 Secure cookie 只用于临时 QA；不是生产 HTTPS 入口。两个临时预览进程均已停止，数据库／会话目录已删除。

截图目录 `docs/screenshots/phase04/`：checks-compact、checks-mobile、check-form-mobile、channel-form-mobile、firing-mobile、acknowledged-mobile、resolved-mobile、events-mobile、events-desktop。均为原始截图，不含凭据。截图后补充了时区说明文字，最终前端构建及组件测试已复验。

SMTP／Webhook 的本地 TLS 测试使用专用临时 CA 并实际验证，不跳过证书校验；这些是自有协议测试端点，不是第三方服务商验收。回环生命周期也不等同于 Linux Agent、ARM64 或 30 分钟实机稳定性证据。

## 8. 安全结果与已知风险

1. 0001–0003、Agent 和锁文件未改；SQLite foreign_keys=1、busy_timeout=5000、WAL、integrity=ok、foreign_key_check 空。
2. 出站目标校验与连接固定、防重定向、独立通知 CIDR 策略、TLS 校验均有测试；已知云元数据地址不可由广泛私网例外覆盖。
3. 通知秘密只存在中心受保护外部文件；API 不接受／返回明文秘密。变更文本扫描未发现实际 Agent Token／私钥；该模式扫描不是通用密码扫描器，见 secret-scan.json。
4. 事件与 Outbox 原子提交；投递是**至少一次**，Webhook 接收方应使用幂等键去重。SMTP Message-ID 稳定但不能保证接收方 exactly-once；成功后、落库前崩溃可能重复发送。
5. 5 次领取尝试后保留 failed，不无限重试；崩溃也消耗一次尝试。渠道禁用或静默不能召回已经开始的投递；静默只影响未来事件的入队，不自动取消旧 Outbox。
6. 过期指标与 unknown 不视为正常；禁用主机／规则不会伪造 Resolved。已确认告警仍保留恢复事件，静默期内的通知决定也进入冷却以避免事件风暴。
7. 原生 DNS 工作总数限制 4；请求能超时／取消，但已运行 getaddrinfo 只能由 OS 结束。SQLite 忙等待和规则批次采用软预算，关闭等待当前短事务收尾；不承诺硬实时停止。
8. 探测／原始指标保留 7 天；事件和投递审计当前不自动清理，后续需要容量规划、备份和长期归档策略。0004 在线表重建需要维护窗口与一致性备份；不要在活动写入中迁移。
9. 真实第三方 SMTP／Webhook 配置、生产 HTTPS、两台 Linux 实机与资源竞争测试均未做。Phase 02-E 的 ARM64 与 30 分钟稳定性证据仍未补齐，不改写为通过。

本阶段没有 SSH 登录云服务器，没有修改 Oracle、Google 的业务、网络、Caddy、sing-box、防火墙、DNS 或 WireGuard，没有生产安装、迁移或发送测试告警。

## 9. 修改文件清单

以下为相对 Phase 03 基线的最终实际路径，另加本报告；既有阶段报告未修改。

```text
.env.example
.gitignore
README.md
docs/evidence/phase04/baseline-python.txt
docs/evidence/phase04/baseline-web.txt
docs/evidence/phase04/browser.json
docs/evidence/phase04/commands.json
docs/evidence/phase04/go-checks.json
docs/evidence/phase04/go-race.txt
docs/evidence/phase04/loopback-lifecycle.json
docs/evidence/phase04/performance.json
docs/evidence/phase04/pytest.txt
docs/evidence/phase04/schema-security-audit.json
docs/evidence/phase04/secret-scan.json
docs/evidence/phase04/web-build.txt
docs/evidence/phase04/web-tests.txt
docs/phase-04-api.md
docs/phase-04-architecture.md
docs/phase-04-test-plan.md
docs/screenshots/phase04/acknowledged-mobile.jpg
docs/screenshots/phase04/channel-form-mobile.jpg
docs/screenshots/phase04/check-form-mobile.jpg
docs/screenshots/phase04/checks-compact.jpg
docs/screenshots/phase04/checks-mobile.jpg
docs/screenshots/phase04/events-desktop.jpg
docs/screenshots/phase04/events-mobile.jpg
docs/screenshots/phase04/firing-mobile.jpg
docs/screenshots/phase04/resolved-mobile.jpg
server/app/agent_api.py
server/app/alerts.py
server/app/config.py
server/app/main.py
server/app/models.py
server/app/monitoring_api.py
server/app/notifications.py
server/app/outbound.py
server/app/retention.py
server/app/scheduler.py
server/migrations/versions/0004_phase04_monitoring.py
server/tests/conftest.py
server/tests/test_phase04.py
server/tools/benchmark_history.py
server/tools/benchmark_phase04.py
server/tools/preview_phase04.py
web/src/App.tsx
web/src/Monitoring.test.tsx
web/src/Monitoring.tsx
web/src/api.ts
web/src/style.css
docs/phase-04-report.md
```

**交付结论：Phase 04 本地功能与测试范围 GO。生产部署未执行、未获授权；停止于本阶段，等待 Phase 05。**
