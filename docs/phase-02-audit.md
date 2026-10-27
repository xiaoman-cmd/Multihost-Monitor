# Phase 02 开发前审计

2026-10-27：初始 Git master 无提交，40 个 Phase 01 文件未跟踪。已逐项检查并建立基线 f595487；不包含 .env、数据库、虚拟环境或 node_modules。基线 pytest：65 passed，1 条上游 Starlette/httpx 弃用告警。

已检查 models.py、main.py、security.py、db.py、0001_phase01、全部 server/tests、agent/README.md、pyproject.toml 和 uv.lock。Python 锁文件包含校验和及公开 PyPI 源；本阶段保留现有 Python 依赖。没有已有 Agent、Linux 主机配置或正式运行数据库。

现有 11 张业务表及 2 张管理员认证表，INTEGER 主键、UTC Unix 秒、FK CASCADE、命名 CHECK／UNIQUE；主机样本按 host_id+sampled_at 唯一，磁盘再按 mountpoint 唯一；会话令牌只存 SHA-256 摘要。每个 SQLite 连接启用 WAL、foreign_keys=ON、busy_timeout=5000。

原有字段不足以表达云厂商、地区、标签、采集白名单和扩展指标。新增 0002_phase02：给 hosts 添加管理配置与受采样时间保护的元数据；新增 agent_samples，提供样本 ID 幂等、内容摘要、完整受 API schema 限制的 JSON 采样、与原 host_metrics 的一对一关联。保留原指标表供兼容查询，批次同时写入三类表。扩展采用 ADD COLUMN／CREATE TABLE，不重建旧表；不修改 0001_phase01。

.gitignore 原已排除 .env、data/、*.db、WAL/SHM、.venv、node_modules。将追加 .env.*（保留 .env.example）、证书／私钥、token 文件、Agent 构建输出规则。

当前为 Intel macOS，无 Go 工具链、无 Linux 测试主机。用户确认暂时没有两台测试主机；真实 Linux 双机、系统工具对比、systemd 安装运行均保持未验收，绝不以本机测试或模拟结果替代。不会进行生产部署。
