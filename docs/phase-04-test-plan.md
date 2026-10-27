# Phase 04 测试与验收计划

所有测试只使用 TemporaryDirectory 文件 SQLite 与自有回环端点。无真实第三方邮件、Webhook、Oracle、Google 连接；不得把此计划或本地通过结论当作生产验收。

## 自动测试映射

| 要求 | 覆盖 |
|---|---|
| HTTP 200/500/超时、TCP 成功/拒绝/超时 | test_real_local_http、test_real_local_tcp_success_refused_and_timeout |
| TLS 信任链／到期 | test_real_local_tls_verification_and_expiry（可信本地 CA，不跳过校验） |
| SSRF／重定向／DNS 重绑定 | test_ssrf_default_rejects、test_dns_all_candidates_checked_and_actual_connect_pinned、独立策略和 metadata 不可覆盖；真实 302 拒绝 |
| CPU、可用内存、磁盘、systemd | test_resource_and_systemd_rules、test_unknown_services_are_missing |
| 离线／恢复、持续异常与滞回 | test_offline_uses_center_time_and_recovers、test_pending_firing_hysteresis_repeat_recovery_restart、边界不重叠 |
| 缺失与正常区分、目标版本切换 | test_missing_is_not_normal_and_does_not_resolve、test_edited_target_does_not_reuse_previous_check_result |
| 去重、确认、静默、冷却 | 并发 8 次评估唯一 Firing；确认幂等；静默不产生 repeat 风暴；恢复仍生成事件 |
| 原子性／数据库忙 | test_rollback_event_outbox_is_atomic、test_database_busy_retries_on_next_cycle_and_preserves_outbox |
| 通知失败／重试／重启 | 租约到期、尝试上限、稳定幂等键与新 Session 恢复 |
| 真实本地通知传输 | 回环 HTTPS Webhook、SMTP implicit TLS、STARTTLS 与 AUTH，固定测试凭据，SMTP Message-ID |
| 通知不能阻塞采样入库 | test_pending_notification_never_blocks_agent_ingestion（传输期间另开写事务并通过实际 Agent API 上传） |
| 密钥保护 | 文件 owner/mode、symlink、path traversal、size 限制；新 API 拒绝密码字段；0004 在 DDL 前拒绝旧明文配置 |
| 调度上限／关闭 | 4 并发实测、取消任务、数据库线程收尾、periodic 异常隔离、lifespan 重启释放锁与双调度器拒绝 |
| 管理权限 | 六类读接口 401；九种写操作缺失 CSRF 403；真实会话管理 CRUD、分页与数值校验 |
| 迁移／清理／回归 | upgrade/check/downgrade/reupgrade 保留旧事件与投递、FK/WAL，探测历史 7 天清理；Phase 01–03 全部回归 |
| 前端 | 空状态、错误、创建默认值与 CSRF、缺失 Firing、确认、外部引用而非密码、内存语义、历史 null |

## 本地执行命令

```sh
.venv/bin/pytest -q
npm --prefix web test
npm --prefix web run build
# 在 agent 目录，使用已安装 Go 工具链：
go test -race -count=1 ./...
go vet ./...
staticcheck ./...
# 根目录，隔离数据库基准：
PYTHONPATH=server .venv/bin/python server/tools/benchmark_phase04.py
# 可选浏览器 QA：私有临时库、实际回环探测，不插入伪造指标
PYTHONPATH=server .venv/bin/python server/tools/preview_phase04.py
```

受限沙箱中需要允许测试监听本机临时端口和读取本机系统信息；不能把权限不足当作代码通过或跳过测试。报告记录最终实际结果、警告与原始输出。

## 浏览器与性能证据

桌面、375px：真实本地 200 → 503 → 200 端点变化，经调度器形成检查与 Firing/Resolved；人工确认、表单、通知引用与历史页面。记录 document.scrollWidth≤innerWidth、console warn/error 和截图。临时库没有 Agent host_metrics 合成数据；这不是云服务器实机验收。

基准使用 50 个合成主机、2 条全局规则共 100 对，原子创建 100 事件／200 Outbox；测量首次评估、5 次稳定评估与领取 16 条 Outbox。只在临时库，无网络发送，不推断生产容量。
