# Phase 03 只读部署准备方案

本文件是准备与回滚设计，**没有执行安装或部署**。正式部署需另行批准、确定 HTTPS 入口、备份窗口和验收方案；不在本阶段修改生产服务器、防火墙、DNS、SSH 或 WireGuard。

## 已实际只读确认

2026-10-27 18:52（Asia/Shanghai）执行 `deploy/phase03-readonly.sh`，原始输出保存在 docs/evidence/phase03/oracle-readonly.txt 和 google-readonly.txt。Google 到 Oracle 443 的 TCP 连通性于 18:56 实测成功，详见 google-to-oracle-connectivity.txt。此结果只代表 TCP 建连，不代表未来监控 HTTPS 域名、证书或 API 已部署验证。

| 项目 | Oracle：中心 + 本机 Agent | Google：远程 Agent |
|---|---|---|
| 地址／用户 | 161.33.189.223 / ubuntu | 34.3.99.64 / phoenix |
| 架构 | x86_64，AMD64 | x86_64，AMD64 |
| 系统 | Ubuntu 24.04.5 LTS，内核 6.17.0-1020-oracle | Ubuntu 26.10，内核 7.3.0-1003-gcp |
| systemd | 255 | 261 |
| Python | 3.12.3 | 3.14.8（Agent 不依赖 Python） |
| 内存总量 | 1,000,345,600 bytes | 997,470,208 bytes |
| MemAvailable 快照 | 467,906,560 bytes | 534,003,712 bytes |
| Swap | 总 2,415,910,912 bytes，已用 160,731,136 bytes | 无 Swap |
| 已有主要服务 | Caddy、FlashSend、TURN、Orbit Foundry API、SSH | sing-box、server-monitor、SSH |
| 已有端口 | 22、80/443、3478、8787，及本机 4180、2019、45125 等 | 22、443、8443，及 DNS 等 |
| 权限 | sudo NOPASSWD: ALL 已只读确认 | sudo NOPASSWD: ALL 已只读确认 |
| 拟用目录 | /opt/multihost-monitor、/etc/multihost-monitor、/var/lib/multihost-monitor 当前未占用 | 同名目录当前未占用 |

根文件系统容量与可用量、inode 详见原始 df 输出；不是从云规格推测。两台均有既有业务；空闲内存快照不构成中心与现有业务竞争时的容量保证。未安装 wg 工具不能据此断言没有 WireGuard，本阶段未改动任何网络接口或配置。

## 部署拓扑与访问入口（待批准）

Mac 仅作为源码构建与管理端。Oracle 运行单个 FastAPI/Uvicorn worker，提供构建后的前端和 API，SQLite 位于本机持久目录。Oracle 和 Google 分别运行专用低权限 Go Agent，均仅出站连接 HTTPS，不监听业务端口。

当前 80/443 已被既有服务使用，不可占用或替换。推荐待批准后选择以下入口之一：

1. 私有入口：中心仅绑定回环，通过受控 SSH 访问，使用管理员显式信任的专用 CA／证书；Agent 也必须校验证书，不能使用 InsecureSkipVerify 或 curl -k。具体 TLS 终止与访问路径在部署时确认。
2. 独立 HTTPS 子域名：用户指定其控制的监控域名，经批准后才添加 Caddy 独立站点，反代到回环 `127.0.0.1:18080`；现有站点、端口与证书保持其各自配置。域名、DNS 和证书链当前未配置／未验证，不能把占位域名写成已可用地址。

不暴露开发 Uvicorn 到公网，不启用 reload、多 worker、Redis、Docker 或 Celery。中心 `MHM_ORIGIN` 必须与实际 HTTPS origin 精确一致，`MHM_COOKIE_SECURE=true`。Caddy 代理不会由当前代码自动配置。

## 候选文件布局与权限

- `/opt/multihost-monitor/releases/<sha>/`：只读版本目录，含 Python 虚拟环境、server 源码与 web/dist。
- `/opt/multihost-monitor/current`：指向已批准版本；切换在维护窗口执行。
- `/var/lib/multihost-monitor/monitor.db`：本机 SQLite 文件及 WAL/SHM，由专用中心用户写入，父目录 0700。
- `/etc/multihost-monitor/server.env`：root 0600，通过 systemd EnvironmentFile 提供；不把管理员密码放命令参数或 Git。
- 中心使用独立无登录用户、单 worker；WorkingDirectory 指向版本根，执行 `.venv/bin/uvicorn app.main:create_app --factory --app-dir server --host 127.0.0.1 --port 18080 --workers 1 --no-proxy-headers`。
- Agent 沿用 agent/packaging/systemd 模板，独立低权限用户，Token 文件 0600，父目录由 root 管理。Oracle／Google 分别签发专用 Token，不共享；配置中使用实际 host_id 和可信 HTTPS 地址。

正式上线前需基于现有 1 GiB 主机实测中心 RSS、SQLite 查询竞争、磁盘增长和 Caddy／TURN 业务负载。可通过独立 systemd 资源限制控制监控进程，但不能为了通过验收将 Agent 改为 root。上述均为方案，未创建用户／unit／目录。

## 版本、迁移与原始数据保留

Phase 02 源码及报告已有独立提交 53ff38f；Phase 03 发布应从报告记录的提交构建，使用锁定依赖和经过测试的静态资源。不要直接在生产源码目录做 npm dev。

正式迁移前暂停写入并用 SQLite backup API 创建一致性备份，保留配置和旧发布。0003 增加三个清理索引，并仅重建没有被其他表引用的 agent_samples 叶表以启用 AUTOINCREMENT；原数据不丢弃，外键全程启用。迁移是维护操作，不承诺与在线写入并行。SQLite 需 >=3.35（窗口函数、MATERIALIZED、DROP COLUMN 支持）。

默认每分钟运行有 5 秒预算的分批清理，每批每表最多 500 行；查询即刻限制为最近 7 天，物理删除可能短暂有积压。清理不可恢复，需备份与明确保留要求。CLI 默认 dry-run：

```sh
# 以下仅是未来已配置环境的操作说明，本阶段未在服务器运行。
PYTHONPATH=server .venv/bin/python -m app.retention
PYTHONPATH=server .venv/bin/python -m app.retention --apply --batch-size 500
```

清理不执行 VACUUM；如需回收文件空间，另选离线维护窗口。

## 正式部署与回滚门槛

部署批准后才执行：备份 → 安装独立版本 → 停止旧中心／Agent 写入 → Alembic upgrade → 启动单进程中心 → 验证可信 HTTPS、登录、CSRF、健康、只读页面 → 分别签发与安全置入 Token → 启动低权限 Agent → 真实样本及系统工具对比 → 故障恢复验收。

失败时：停止新中心及新 Agent，保留日志与 SQLite 一致性备份，恢复旧 current 和旧环境；如数据库已迁移，维护窗口中可 downgrade 0002_phase02（仅回滚 Phase 03 索引和 ID 策略）或恢复完整已验证备份，再启动旧中心。清理已经删除的数据只能从备份恢复。不得为回滚停止 FlashSend、TURN、sing-box、WireGuard 或更改全局防火墙。

原 Phase 02-E 真实 Linux 双机、ARM64 和 30 分钟稳定性证据仍未补齐；本阶段的 UI、性能合成基准和本机 macOS 截图不替代这些证据。
