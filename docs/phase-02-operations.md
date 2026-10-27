# Phase 02 操作与验收手册

## 中心升级与回滚

中心沿用 Phase 01 同源管理会话和开发脚本。升级前停写，并使用 SQLite backup API 或 `sqlite3 monitor.db '.backup backup.db'` 创建一致性备份；不得只拷贝正在写入的 .db 而遗漏 WAL。备份、.env 和私钥限制权限。

```sh
uv sync --frozen
uv run --frozen alembic upgrade head
# 正式部署应在现有受信 HTTPS 反向代理后运行，单 worker。
./deploy/dev.sh
```

新增版本 `0002_phase02`，原 `0001_phase01` 未改动。回滚前停止所有 Agent 并备份；`alembic downgrade 0001_phase01` 会删除扩展采样 JSON 和 hosts 的新增配置／元数据列，但保留原 host_metrics、disk_metrics 与主机数据；再切回 Phase 01 基线 `f595487`。当前 SQLite 必须 >=3.35，降级使用原生 DROP COLUMN，不在开启外键时重建 hosts 父表。完整灾难恢复优先恢复已验证的备份。

## 创建主机与发放凭据

通过 `/api/auth/login` 获取现有 HttpOnly 管理会话和 csrf_token。所有管理写 API 必须携带正确 Origin 与 X-CSRF-Token；不要将 Cookie、管理员密码或 Agent Token 放入命令行历史、URL、截图或版本控制。

`POST /api/v1/hosts` 请求示例（不含凭据）：

```json
{"name":"linux-a","hostname":"linux-a","cloud_provider":"local","region":"lab","tags":["phase02"],"systemd_units":["sshd.service"]}
```

响应含 `host.id` 与 `agent_token`，Token 只此时显示。列表和查询不会再次返回明文。保存到专用主机的私有 Token 文件；若丢失，管理员调用 `POST /api/v1/hosts/{id}/token/rotate`。轮换在单个事务中撤销全部旧 Token，`POST .../token/revoke` 撤销当前凭据。不要让 Agent 自行创建主机。

## Linux 安装（只供已批准的测试主机手工执行）

先在开发机 `cd agent && make build`，校验产物 SHA-256，并按 `uname -m` 选择 amd64 或 arm64。不把 macOS 二进制放到 Linux。以下是需要主机管理员执行的安装示例，不会由应用自动远程执行：

```sh
sudo useradd --system --user-group --no-create-home --shell /usr/sbin/nologin multihost-agent
sudo install -d -m 0755 /usr/local/lib/multihost-monitor
sudo install -m 0755 monitor-agent-linux-amd64 /usr/local/lib/multihost-monitor/monitor-agent
sudo install -d -o root -g multihost-agent -m 0750 /etc/multihost-monitor
sudo install -o root -g multihost-agent -m 0640 config.example.json /etc/multihost-monitor/config.json
sudo install -o root -g root -m 0644 monitor-agent.service /etc/systemd/system/monitor-agent.service
```

通过安全文件传送流程置入 Token，设所有者为 `multihost-agent:multihost-agent`，模式 `0600`；父目录 root 管理并仅允许专用组遍历。不要通过 `echo TOKEN`、命令参数或终端日志传递。config.json 填真实 host_id、中心 HTTPS origin、允许挂载点／接口和本地 systemd 白名单。私有 CA 可在 ca_file 中指定，不能关闭证书验证。系统 trust store 必须可用。

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now monitor-agent.service
sudo systemctl status monitor-agent.service
sudo journalctl -u monitor-agent.service --since '5 minutes ago'
```

Agent 服务以专用低权限用户运行、无 capabilities，仅读取系统信息和 `systemctl show` 的白名单服务状态。不要为缺失指标放宽为 root。ProtectHome/PrivateTmp 会使部分挂载点不可见或位于隔离视图，应按监控目标验证，无法访问的文件系统被跳过。不能把未采集项解释为零。

## Token 轮换、升级、卸载

轮换后通过受保护渠道写入同目录临时文件，设置私有模式与所有者，再原子替换 agent.token；旧 Token 立即失效。认证失败进程会退出，部署的 systemd 每 30 秒重启；也可由管理员手工重启以立即恢复。Agent 本身不重启任何服务。

升级前保留旧二进制及其校验和，管理员停止 Agent、替换新二进制、启动；失败则恢复旧二进制。队列不持久化，停止时未发送样本会丢失，操作前确认中心已收齐。

卸载：管理员 `systemctl disable --now monitor-agent.service`，删除 unit 和二进制，daemon-reload；中心撤销 Token，按组织密钥处置流程清理私有配置，再移除不再使用的专用账户。不要自动删除中心主机历史数据。

## 两台真实 Linux 验收（本次尚未执行）

在各自已批准的 Linux 测试主机完成以下记录，避免提交敏感资产名称、IP 或凭据：

1. 记录脱敏主机标识、CPU 架构、OS、内核、二进制 SHA-256、Go 构建版本、host_id。
2. 等待至少两个 30 秒周期，管理员查询 `/api/v1/hosts/{id}/metrics`；记录 sample_id、sampled_at、received_at、last_seen_at、元数据和采样数量。
3. 同一时间窗口记录 `vmstat 1 5` / `mpstat 1 5`、`free -b`、`df -B1`、`df -i`、`ip -s link` 或 `/proc/net/dev`、`uptime`、`systemctl show --property=ActiveState`。CPU 为两次采样间平均，网络为单调时间窗口的差值；磁盘保留块与内存 available 定义应说明，不能直接比较不同时间点或不同定义。
4. 重放保存的同一批次，确认 duplicates 增加且计数不变；测试错误／撤销 Token 与 A Token 上报 B host_id 均返回统一 401。
5. 在批准的测试中心停服务，确认 Agent 持续采集并记录重试；恢复中心，确认缺失 sample_id 补报。小容量测试配置验证丢最老样本和 dropped_total，不对生产断网。
6. 手工重启 Agent，确认重新建立基线并恢复；明确重启前未发队列不持久化。用受控网络接口计数重置验证 null 速率和下一窗口恢复，不能为测试而改生产网络。
7. 记录 SQLite `PRAGMA journal_mode`、`foreign_keys`、`foreign_key_check`、实际数量与工具对比误差。不要仅凭“Agent 正在运行”判定数据正确。

以上是验收操作清单，不是已经完成的证据。当前报告须维持真实双机验收未完成。
