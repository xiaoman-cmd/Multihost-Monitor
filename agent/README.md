# Go Agent · Phase 02

只读采集、认证上报、有限内存队列。支持构建 Linux AMD64 / ARM64，默认每 30 秒采样。使用 gopsutil/v4；不需要 root、不执行 Shell、不接受远程命令、不更改防火墙或服务状态。

## 构建与检查

需要 Go 1.24+、Linux 静态二进制构建无需 CGO；race detector 在有 C 编译器的受支持开发平台运行。

```sh
cd agent
go mod verify
go test -race ./...
go vet ./...
# 工具检查版本，本次使用 staticcheck v0.8.2
staticcheck ./...
make check
make build
```

构建产物在 `agent/bin/`，不提交 Git。配置模板在 `config.example.json`，systemd 模板在 `packaging/systemd/monitor-agent.service`。安装／卸载／回滚和验收步骤见 [操作手册](../docs/phase-02-operations.md)。

首次采样只建立 CPU／网络基线，不生成零 CPU 演示样本；约 30 秒后首条有效采样可上报。内存 used 定义为 total−available，与后端原模型兼容。磁盘 available 使用 gopsutil Free（通常对应用户可用块），允许 total−used 与 available 因保留块而不同。没有 inode 的文件系统返回 null。接口新增、消失、计数回退或首条采样速率为 null；缺失／无权限的文件系统被跳过，不伪造容量。

队列默认 240 条（约两小时采样），上限 2048 条，满时丢弃最老队列条目（可能仍被发送快照持有）；dropped_total 统计队列驱逐次数，不代表最终未入库条数。发送任务复制最多 32 条快照，只有完整确认后按 sample_id 删除。网络失败、408、429 和 5xx 指数退避并抖动；批次 409/422 等不可重试错误会拆成单条隔离，丢弃被拒绝样本并记录无敏感内容日志。401/403 停止进程并等待运维修复，systemd 按配置重启。HTTPS 使用系统 CA 或附加 CA 文件；禁止跳过校验和重定向。每次请求重新读取私有 Token 文件，支持原子替换后的轮换。

采集和发送独立，离线不会阻止采集；SIGTERM 取消采集、HTTP 和退避等待并记录未发送数量。队列仅在内存中，进程退出／重启会丢失缓冲，不提供持久化补报承诺。重启重新建立 CPU／网络基线并恢复采集。远程 config 只能指定已在本地配置白名单内的 systemd unit 交集；远程不能扩展本地权限。

本次开发环境为 macOS；本机真实采集烟测不等于两台真实 Linux 主机验收。详见 [阶段报告](../docs/phase-02-report.md)。
