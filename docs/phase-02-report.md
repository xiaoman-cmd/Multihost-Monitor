# MultiHost Monitor V1.0 — Phase 02 阶段报告

日期：2026-10-27（Asia/Shanghai）。结论：**CONDITIONAL GO**。开发实现、自动测试、双架构构建及本机真实进程集成已具备交付条件；两台真实 Linux 主机、Linux 系统工具数值对比和 systemd 实机验收尚未完成，不能宣布 Phase 02 全量集成验收通过，也不是生产部署批准。

## 1. 开发前审计与基线

先审计实际代码、迁移、测试、依赖锁文件及 Git 状态，再开始实现，记录见 [审计摘要](phase-02-audit.md)。仓库初始无提交，已按要求建立 Phase 01 基线 **f595487**（40 文件），无密钥、数据库或本地环境配置。基线原有 pytest 65 项全部通过。

`.gitignore` 已验证排除 `.env`、`.env.local`、SQLite DB/WAL/SHM、私钥／Token、Agent 本地配置、二进制与依赖。`pyproject.toml`、`uv.lock`、`server/app/security.py`、`server/app/db.py` 原样保留；沿用 FastAPI、SQLAlchemy 2.0、Alembic、SQLite WAL 和管理员会话。没有重建项目，没有 Docker、Redis 或 Celery。

## 2. 实际实现范围

- 管理员创建／配置／分页查询主机：名称、hostname、云厂商、地区、标签、systemd 白名单。
- 360-bit 随机 Agent Token，仅创建／轮换响应显示；数据库仅存 SHA-256 摘要；轮换原子撤销旧凭据；支持主动撤销及过期校验。
- Bearer 与 host_id 强绑定；错误／撤销／过期／禁用主机／跨主机身份均返回统一 401；Agent 无注册与管理能力。
- Go 独立模块及 cmd、config、collector、transport、queue、runtime、model、systemd 目录；gopsutil/v4 采集 CPU、load1/5/15、内存 total/available、swap、磁盘 total/used/available、inode、网络累计量／速率、uptime、OS／架构／版本和白名单服务状态。
- 固定 30 秒采样节拍，独立采集／发送；首次 CPU 建基线不发虚构样本；网络首条／新增／计数回退用 null 速率；文件系统无法访问则跳过；systemd 不可用返回 unavailable。
- 批量 HTTPS、固定请求超时、禁止重定向、系统 CA／附加 CA 校验、有限队列、指数退避和抖动、按 ID 确认、不可重试坏样本隔离、SIGTERM 取消和退出统计。
- heartbeat、metrics、config 与管理员指标查询；完整受限 JSON 采样与兼容的原指标／磁盘表在一个事务写入；成功提交才更新在线时间；旧补报不能回退主机元数据。
- 安装／卸载／升级／回滚、低权限 systemd 模板与配置模板。

本阶段没有开发正式 Dashboard 图表、告警引擎、通知服务、SSH 控制、远程命令执行或自动服务重启。前端维持 Phase 01，主机管理与指标验证通过 API 完成。

## 3. 数据库迁移

新增 **0002_phase02**，down_revision 为 0001_phase01：hosts 增加 8 个配置／元数据列；新增 agent_samples，用 UNIQUE(host_id,sample_id)、UNIQUE(host_id,sampled_at)、唯一 host_metric_id、FK 和 CHECK 约束保存幂等身份及完整扩展 JSON。JSON 具体结构和数值约束由严格 API schema 验证，未声称每个 JSON 数值都有 SQL CHECK。

采用 ADD COLUMN / CREATE TABLE，升级不重建原表。降级要求 SQLite >=3.35，用原生 DROP COLUMN 保留旧 hosts／host_metrics／disk_metrics 数据；会删除扩展 JSON 和新增配置列，必须先备份。已验证带 Phase 01 数据的升级、降级与再升级；模型对比无漂移，离线 SQL 生成成功。

**0001_phase01 未修改**，与基线逐字节相同，SHA-256：`6049a1cf778d5fe7f3b44c94ad258a52577a5ecb88af4a3204ff732203a4de8d`。

SQLite 继续在每个应用连接启用 foreign_keys=ON、busy_timeout=5000 和 WAL。BEGIN IMMEDIATE 将凭据校验、撤销、幂等检查和入库串行化，避免重复及撤销竞态；忙／存储不可用统一 503 + Retry-After。

## 4. API 清单

| 方法 | 路径 | 身份／语义 |
|---|---|---|
| POST | /api/v1/hosts | 管理会话 + Origin + CSRF；201，Token 仅此时显示 |
| GET | /api/v1/hosts | 管理会话；offset/limit |
| PUT | /api/v1/hosts/{id} | 管理会话 + Origin + CSRF；配置替换 |
| POST | /api/v1/hosts/{id}/token/rotate | 管理会话 + Origin + CSRF；新 Token 一次显示 |
| POST | /api/v1/hosts/{id}/token/revoke | 管理会话 + Origin + CSRF；204 |
| GET | /api/v1/hosts/{id}/metrics | 管理会话；since/until/limit/before_id |
| GET | /api/v1/agent/config?host_id={id} | Bearer；固定节拍、批次上限和服务白名单 |
| POST | /api/v1/agent/heartbeat | Bearer；合法数据提交后更新在线时间 |
| POST | /api/v1/agent/metrics | Bearer；200 accepted/duplicates；整批事务 |

原 Phase 01 接口保留。详细字段、限制和重试语义见 [协议说明](phase-02-protocol.md)。

## 5. 测试数量和结果

| 验证 | 实际结果 |
|---|---|
| Python pytest | **100 passed**，1 条已有 Starlette/httpx 上游弃用告警 |
| Phase 01 回归 | 原有 65 项通过；仅迁移表清单增加 agent_samples 预期，未删除／放松原测试 |
| Phase 02 Python | 新增 35 项：管理 CSRF、Token 摘要／轮换／撤销／过期、越权、原子回滚、幂等、并发重复、历史保护、目标／范围／时间／条数校验、流式大小和读取超时、查询、带数据迁移和级联约束 |
| Go 单元／集成测试 + race | **19 passed**（[原始输出](evidence/phase-02-go-race.txt)），darwin/amd64，未发现数据竞争；TLS 传输使用真实本地 TLS listener，runtime 故障测试包含测试替身并明确不算 Linux 验收 |
| go vet | darwin/amd64、linux/amd64、linux/arm64 通过 |
| staticcheck | **2026.2.2 (0.8.2) 通过**；最初发现测试中的 HTTP 状态数字字面量，已修复并复验 |
| gofmt | 无未格式化文件 |
| Go 模块校验 | go mod verify：all modules verified；go.sum 已提供 |
| Linux AMD64 / ARM64 | 两种 CGO_ENABLED=0 静态 ELF 构建成功，**没有在 Linux 上执行** |
| 前端回归 | 3 项组件测试通过，TypeScript + Vite 生产构建成功 |
| Alembic | 升降级、再升级、原数据保留、模型一致性、alembic check、离线 SQL 通过 |
| 本机真实进程集成 | Go Agent → 受信本地 CA HTTPS → FastAPI → SQLite；首样本、停服缓冲、恢复补报、重启恢复通过；这是 macOS，不是 Linux 双机验收 |
| 语法／脚本 | Python compileall、bash -n、git diff --check 通过 |

测试环境：macOS 13.1 x86_64、Python 3.12.13、SQLite 3.53.1、Go 1.27.3 darwin/amd64、gopsutil/v4 v4.25.9。Go 工具链来自官方发布镜像，SHA-256 验证成功；未修改系统工具链。Python 依赖锁文件未改动。

可复验：根目录 `.venv/bin/pytest -q`；agent 下 `go test -race ./...`、`go vet ./...`、`staticcheck ./...`、`go mod verify`、`make check`、`make build`。本机 HTTPS 集成工具为 `server/tools/agent_e2e.py`，使用临时测试凭据、数据库与证书并自动清理进程；需显式提供本机编译的 MHM_AGENT_BINARY。工具不支持或执行远程生产部署。

## 6. 性能测量

[原始入库测量](phase-02-performance.json)：本机 TestClient + 文件 SQLite WAL，20 批 × 32 条 = 640 条合成测试样本，每批约 20,219 字节。**批次中位 64.69 ms，P95 66.47 ms，总请求时间 1.346 s，约 475.6 样本／请求秒**。不含网络、TLS、真实 Linux 采集开销，不是生产容量或多主机 SLA。

[Go 测量和构建证据](phase-02-go-evidence.json)：Intel i7-9750H，队列 Push/Peek/Ack 基准 **488.1 ns/op、240 B/op、1 alloc/op**。默认队列 240 条，上限 2048 条，发送快照最多额外持有 32 条；目标数量与字符串长度有上限。未进行生产规模并发或长时间 RSS／磁盘增长测量。

## 7. 安全检查结果

- 无匿名注册；Agent 与管理员认证分离；管理写操作复用已有会话、Origin 和 CSRF。
- Token 不进入 URL、CLI 参数或日志，未落入源码；数据库仅摘要。Token 文件实际打开后检查为普通文件且 group/other 无权限，每次请求重新读取以支持原子轮换。
- HTTPS 强制；TLS 最低 1.2，证书与主机名校验；禁止重定向；无 InsecureSkipVerify；本机 CA 正常验证与错误信任拒绝均有测试。
- 无 Shell、SSH、远程任意命令、防火墙修改或自动服务控制。唯一外部调用是固定 `/usr/bin/systemctl show ... -- <validated-unit>`；本地和中心白名单双重限制。
- systemd 采用专用低权限用户、空 capabilities、NoNewPrivileges、ProtectSystem 等限制，Token 私有文件和 root 管理父目录；模板尚未在真实 Linux 验证。
- 请求上限 1 MiB（实际流字节）、15 秒读取时限、每批 32 条、每类目标 32 个、严格类型和有限数值／时间范围；非法整批零写入。
- 当前日志只记录固定事件、计数和退避时长；本机集成确认 Token 未出现在 Agent 日志。
- SQLite 外键与 WAL 保持正常；没有未经批准的生产部署。

## 8. 真实主机验收证据与矩阵

**两台真实 Linux 主机证据：无。** 用户明确回复“暂时没有，记录真实主机验收未完成”。没有使用 Mock 或容器冒充真实主机。本机额外证据见 [macOS HTTPS 集成](phase-02-local-e2e.json)，只用于验证进程、协议和恢复链路。

| 要求 | 本次状态 |
|---|---|
| 1. Agent 正常采集 | 本机 macOS 真实采集通过；Linux 待验收 |
| 2. 两台真实 Linux 上报 | **未完成** |
| 3. 与 Linux 系统工具基本一致 | **未完成**，未生成虚构误差数据 |
| 4. 重复上传不重复 | API 与并发测试通过 |
| 5. 错误 Token 拒绝 | 通过 |
| 6. 已撤销 Token 拒绝 | 通过 |
| 7. A Token 不能写 B | 三个 Agent API 绑定测试通过 |
| 8. 中心停止不崩溃 | 本机真实 HTTPS 进程验证通过；Linux 待验收 |
| 9. 恢复后缓冲补报 | 本机真实进程验证通过；Linux 待验收 |
| 10. 缓冲溢出 | 队列／runtime 测试通过，驱逐最老条目，in-flight ACK 不误删新条目 |
| 11. 网络计数重置 | 差分算法测试通过，null 速率；Linux 受控实测待验收 |
| 12. 重启恢复 | 本机真实进程通过；重启会丢失内存队列，不承诺跨重启补报 |
| 13. SQLite FK / WAL | 自动测试与本机入库通过 |
| 14. Phase 01 回归 | 通过 |
| 15. race / vet / 静态检查 | 通过，race 运行平台为 macOS |
| 16. 双架构构建 | 通过，执行验收仍待 Linux 主机 |

## 9. 未完成事项与风险

1. 真实 Linux 双机、CPU／内存／磁盘／网络与系统工具的同窗口对比、低权限 systemd 安装与资源隔离视图验证，需按 [操作手册](phase-02-operations.md) 在获准测试环境执行。未部署生产。
2. 内存队列不持久化：满时驱逐最老条目，退出／重启丢失未发送数据；dropped_total 是队列驱逐次数，in-flight 快照可能仍送达。若需要跨重启恢复，需后续独立设计磁盘 spool。
3. JSON 扩展样本有体积和保留成本，未实现自动保留策略、长期压测或生产备份流程；SQLite 单写者与同步写锁构成吞吐上限。
4. online 状态表示最近接收过成功上报，尚无离线状态调度器，查询者需使用 last_seen_at 判断新鲜度；不引入告警引擎。
5. 秒级 sample timestamp 冲突、样本内容冲突返回 409；历史最多 7 天、未来最多 120 秒，要求主机时钟同步。被拒绝单条会被丢弃并记录计数，需要运维观察。
6. 服务白名单后续变更可能使旧缓冲中的服务目标不再允许，旧样本会按 422 被隔离丢弃；此设计优先执行当前授权，不能绕过白名单。
7. 仍需生产 HTTPS 代理层的并发／连接／速率控制与可信来源策略；本阶段不是分布式限流或防 DoS 方案。原 Phase 01 管理员密码轮换、MFA 等边界仍存在。

## 10. 交付与结论

**CONDITIONAL GO：代码和开发验证可交付，Phase 02 全量验收仍以两台真实 Linux 主机证据为条件。** 没有将计划、测试替身、未执行的 Linux 二进制或系统工具比较写成已通过。当前仅提交 Phase 01 基线，Phase 02 源码改动保留在工作区供审阅。

## 修改与新增文件清单

- `.gitignore`
- `README.md`
- `agent/Makefile`
- `agent/README.md`
- `agent/cmd/monitor-agent/main.go`
- `agent/config.example.json`
- `agent/go.mod`
- `agent/go.sum`
- `agent/internal/collector/collector.go`
- `agent/internal/collector/collector_test.go`
- `agent/internal/config/config.go`
- `agent/internal/config/config_test.go`
- `agent/internal/model/model.go`
- `agent/internal/queue/queue.go`
- `agent/internal/queue/queue_test.go`
- `agent/internal/runtime/runtime.go`
- `agent/internal/runtime/runtime_test.go`
- `agent/internal/transport/transport.go`
- `agent/internal/transport/transport_test.go`
- `agent/packaging/systemd/monitor-agent.service`
- `docs/evidence/phase-02-go-race.txt`
- `docs/phase-02-audit.md`
- `docs/phase-02-go-evidence.json`
- `docs/phase-02-local-e2e.json`
- `docs/phase-02-operations.md`
- `docs/phase-02-performance.json`
- `docs/phase-02-protocol.md`
- `docs/phase-02-report.md`
- `server/app/agent_api.py`
- `server/app/agent_schema.py`
- `server/app/config.py`
- `server/app/main.py`
- `server/app/models.py`
- `server/migrations/versions/0002_phase02_agent_host_configuration_and_atomic_.py`
- `server/tests/test_migrations.py`
- `server/tests/test_phase02.py`
- `server/tools/agent_e2e.py`
- `server/tools/benchmark_ingest.py`
