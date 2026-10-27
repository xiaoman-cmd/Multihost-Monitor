# MultiHost Monitor V1.0 — Phase 02-E 报告

日期：2026-10-27（Asia/Shanghai）。**状态：按用户指示停止实机验收。最终结论：CONDITIONAL GO，不能宣布完整 GO。**

用户最新指示为“继续开发，不用验证”。收到后停止远程验收准备，未安装／运行测试 Agent，未创建远程测试用户、目录、Token、systemd unit 或 SSH 转发隧道；未修改既有业务、SSH、WireGuard 或防火墙。没有执行 30 分钟稳定性测量。后续功能开发范围等待用户指定。

## 1. 代码与 Git 候选

初始工作区为 Phase 02 未提交改动，基于 Phase 01 的 f595487。已检查并复验 Python、Go、迁移和前端，建立 Phase 02 候选提交：

- **53ff38f48a49f3bc0897550be4950b995a5c1599** — Phase 02 candidate: authenticated Go telemetry ingestion and agent。
- **b13d85a3e53a6f68c20baca4a11859dc1c31e11d** — 仅增加采集／上报耗时、队列统计日志，以及未部署的隔离验收工具与本地工具测试。

没有引入新数据库迁移，没有修改 0001_phase01。`.env`、`.env.local`、Agent 本地配置、私钥、Token、SQLite 数据文件及二进制被 .gitignore 排除；没有读取、输出或提交 SSH 私钥内容或真实 Agent Token。

候选暂存格式检查曾提示 0002 迁移末尾多余空行，属于格式告警，不是测试失败；未为此修改已使用的迁移文件。

## 2. 已实际执行的本地复验

以下均在用户停止指示之前完成，不是 Linux 实机验收结果：

| 项目 | 结果 |
|---|---|
| Phase 02 Python 原有测试 | 100 passed，1 条上游 Starlette/httpx 弃用告警 |
| 加入隔离中心工具测试后 | 103 passed；新增 3 项仅验证本地故障控制工具 |
| Go race detector | 19 passed，darwin/amd64，未发现数据竞争 |
| go vet / staticcheck / gofmt | 通过 |
| Go module 校验 | all modules verified |
| Linux AMD64 / ARM64 构建 | 成功；本阶段未在 Linux 执行二进制 |
| 前端 | 3 项组件测试通过，TypeScript + Vite 构建成功 |
| Alembic | upgrade head、check 通过，无模型漂移 |

Go 原始输出：[preflight-go-race.txt](evidence/phase02e/preflight-go-race.txt)。

实际命令（缓存与工具链环境在本机单独设置，不含密钥值）：

```sh
.venv/bin/pytest -q
cd agent
go test -race -count=1 -json ./...
go vet ./...
/private/tmp/mhm-tools/staticcheck ./...
make check
make build
go mod verify
cd ..
npm --prefix web test
npm --prefix web run build
MHM_DATABASE_URL=sqlite:////private/tmp/mhm-phase02e-migration.db .venv/bin/alembic upgrade head
MHM_DATABASE_URL=sqlite:////private/tmp/mhm-phase02e-migration.db .venv/bin/alembic check
git check-ignore .env .env.local agent/config.json agent/bin/monitor-agent-linux-amd64 private.key private.pem actual.token data/monitor.db
```

## 3. 实际 Linux 主机识别

没有假定架构；分别通过已授权 SSH 只读执行 uname 和系统版本查询。结果两台均为 AMD64，**没有 ARM64 主机**。

| 项目 | Google Cloud | Oracle Cloud |
|---|---|---|
| 地址 | 34.3.99.64 | 161.33.189.223 |
| 登录用户 | phoenix | ubuntu |
| uname -m | x86_64 | x86_64 |
| 系统 | Ubuntu 26.10 | Ubuntu 24.04.5 LTS |
| 内核 | 7.3.0-1003-gcp | 6.17.0-1020-oracle |
| 逻辑 CPU | 2 | 2 |
| systemd | 261 | 255 |
| 既有服务示例 | ssh、sing-box、server-monitor | ssh、Caddy、FlashSend、TURN、Orbit Foundry API |

Oracle 使用用户指定的 10-02 密钥。两台主机均有正在运行的服务，因此未将它们当作可随意修改的空白测试机。已提出专用用户／独立目录／回环隧道方案，但未收到该安装方案的授权，随后用户要求不用验证，因此没有实施。

只读观测摘录：[readonly-host-observations.json](evidence/phase02e/readonly-host-observations.json)。该文件是实际 SSH 输出中选取的原值，不是完整终端录像，也不是 Agent 上报证据。工具时刻分别为 2026-10-27 18:29:00 和 18:29:07（Asia/Shanghai）。

实际只读连接命令：

```sh
ssh -i /Users/phoenix/.ssh/google_compute_engine -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=yes phoenix@34.3.99.64 'uname -m; cat /etc/os-release; id; uptime; command -v python3; command -v systemctl; sudo -n true && echo SUDO_AVAILABLE; systemctl list-units --type=service --state=running --no-pager --plain; ss -lntup'
ssh -i /Users/phoenix/.ssh/flashsend_oracle/ssh-key-2026-10-02.key -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=yes ubuntu@161.33.189.223 'uname -m; cat /etc/os-release; id; uptime; command -v python3; command -v systemctl; sudo -n true && echo SUDO_AVAILABLE; systemctl list-units --type=service --state=running --no-pager --plain; ss -lntup'
```

另外只读执行了 `date -u +%FT%TZ`、`uname -r`、`getconf CLK_TCK`、`nproc`、`free -b`、`df -B1 /`、`df -i /`、`systemctl --version`、`sudo -n /usr/sbin/sshd -T` 的特定转发字段过滤，以及工具存在性和测试目录未占用检查。没有更改 SSH 配置。Google 的附加检查中 rg 不存在，后续没有将缺失输出误记为转发策略验证成功。

## 4. 指标对比

仅有系统工具的只读盘点值，**没有 Agent 同窗口采样**；不能计算误差或声称指标一致。

| 指标（系统工具原值） | Google | Oracle | Agent 值／误差 |
|---|---:|---:|---|
| MemTotal bytes | 997470208 | 1000345600 | 未采集／N/A |
| MemAvailable bytes | 540667904 | 470261760 | 未采集／N/A |
| SwapTotal bytes | 0 | 2415910912 | 未采集／N/A |
| 根文件系统容量 bytes | 30009217024 | 47321268224 | 未采集／N/A |
| 根文件系统可用 bytes | 27927101440 | 40570466304 | 未采集／N/A |
| 根 inode 总数 | 3637760 | 5980160 | 未采集／N/A |
| 根 inode 已用 | 64287 | 147559 | 未采集／N/A |
| CPU／load／网络速率／uptime 对比 | 未执行 | 未执行 | N/A |
| systemd 白名单上报对比 | 未执行 | 未执行 | N/A |

## 5. systemd 验收

全部保持**未执行**：专用 Agent 用户、Token 权限、ProtectSystem／NoNewPrivileges、start/stop/restart、自启动配置、SIGTERM、Agent 无监听端口及前后业务对比。只读识别到 systemd 和既有服务可用，不能替代 Agent systemd 实机验收。

未给 Agent root 权限，因为本阶段没有部署 Agent。没有重启任何既有服务或主机。

## 6. 故障恢复

Linux 实机的中心不可达、HTTPS 超时、503、恢复补报、Agent 重启、Token 撤销、网络计数器回退、服务不存在和白名单变更均**未执行**。不能以 Phase 02 的本机 macOS 测试或新工具的本地单测替代。

已准备但未部署的工具：`deploy/phase02e/center.py`（隔离中心 503／超时注入）和 `deploy/phase02e/observe.py`（只读进程／系统观测）。工具不构成故障恢复成功证据。

## 7. 性能与稳定性

| 要求 | Google | Oracle |
|---|---|---|
| Agent RSS／CPU | 未测 | 未测 |
| 每分钟网络发送 bytes | 未测 | 未测 |
| 采集耗时／上报耗时 | 未测 | 未测 |
| 队列长度／丢弃数量 | 未测 | 未测 |
| 连续稳定性时长 | 0 分钟 | 0 分钟 |

为将来的测量加入了日志 `duration_ms`，并在成功确认后记录实际队列长度与累计驱逐次数。这是观测能力开发，不是性能测量结果。既有 Phase 02 合成基准值未挪用为 Linux 性能。

## 8. 修改文件与提交

Phase 02 候选 53ff38f 的 38 个文件清单见提交和 [Phase 02 报告](phase-02-report.md)。本阶段最小观测与准备提交 b13d85a 包含：

- `agent/internal/runtime/runtime.go`：采集／上报耗时，以及确认后的队列／丢弃统计。
- `deploy/phase02e/center.py`：仅供隔离验收的故障控制入口，未部署。
- `deploy/phase02e/observe.py`：只读 Linux 系统／Agent 进程观测脚本，未在远程运行。
- `server/tests/test_phase02e_harness.py`：本地隔离工具单测，不是实机验收。
- `docs/evidence/phase02e/preflight-go-race.txt`：本次 Go 预检输出。

本报告及只读盘点摘录单独保存；没有密钥、真实 Token、正式数据库或本地环境配置。

## 9. 未完成事项与结论

缺少 ARM64 实机；两台 AMD64 Agent 运行、同窗口指标比较、systemd、故障恢复和至少 30 分钟稳定性全部未验收。按最新指示不再为此安装或执行测试。

**CONDITIONAL GO：Phase 02 代码候选和本地回归可交付；Phase 02-E 实机验收未完成，不是完整 GO。** 后续开发阶段与功能清单尚待用户明确，未擅自进入 Phase 03。
