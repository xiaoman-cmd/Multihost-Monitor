# Phase 03 API 契约与实现边界

## 审计

基线 a66401e，Phase 02 源码／报告独立提交 53ff38f，最小观测改动 b13d85a。沿用 SQLAlchemy、SQLite WAL、Agent UUID v4 采样、受限 JSON 扩展数据和管理员 HttpOnly 会话。现有 0001／0002 不修改；0003 新增三个 sampled_at+id 清理索引，并为 agent_samples 启用 AUTOINCREMENT 防止保留清理后 ID 复用扰动分页快照；仅重建无被引用方的 agent_samples 叶表，保留原行并验证外键。不引入新运行依赖。

旧 /api/hosts 与 /api/v1/hosts、Agent 三个接口保持兼容；新 Dashboard 使用以下受管理员会话保护的接口。所有秒数为 UTC Unix 秒；页面可按本地时区格式化显示并标明时区。

| 方法 | 路径 | 契约 |
|---|---|---|
| GET | /api/v1/dashboard | search、status=all/online/offline/disabled、provider、tag、after_id、limit≤50；全局 stats、过滤后 total、主机 items、next_after_id、server_time、offline_after_seconds=90、provider facets |
| GET | /api/v1/hosts/{id} | 管理配置、有效在线状态、最新完整采样、指标新鲜度；不返回任何凭据 |
| GET | /api/v1/hosts/{id}/history | range=1h/6h/24h/7d，默认桶 30/60/300/1800 秒；最多 1200 个连续段桶点，过多返回明确 422，不能静默截断；server_time、from、until、bucket_seconds、points |
| GET | /api/v1/hosts/{id}/samples | since/until≤7 天，limit≤200；以 sampled_at+id 升序 keyset 分页，snapshot_id 固定首请求入库上界，after_time+after_id 成对；after_time/id 和 snapshot_id 组合用于续页 |
| PATCH | /api/v1/hosts/{id}/state | 管理会话 + Origin + CSRF，JSON {disabled:bool}；禁用拒绝 Agent 认证，启用不执行远程服务控制 |

创建、配置、轮换和撤销继续使用 Phase 02 原有管理 API。Token 仅在操作响应后存页面内存，一次展示、关闭／跳转／退出／5 分钟后清除；不写 localStorage、sessionStorage、日志或 URL。

## 状态与数据

disabled 优先；其余 last_seen_at≥server_time−90 为 online，其他（包括未接入）为 offline。不依赖 Agent 时钟、不依赖数据库遗留 online/offline 字符串。总数包含禁用，离线数不含禁用。查询用同一服务端时间计算。指标新鲜度独立判断：可能“Agent 在线但只补报历史数据”，此时指标必须显示过期。没有样本时无指标，不生成零值。

最新采样取最大 sampled_at（而非入库 ID），不让旧补报覆盖新指标。CPU／内存／磁盘百分比和网络合计只从已保存的采样计算；任一接口速率未知则该方向合计未知。网络累计为当前上报接口计数合计，含虚拟／回环接口，不代表账单流量。

## 历史与缺口

SQLite 窗口函数按 sampled_at 检测 >90 秒间隔，连续段分别聚合，绝不跨段平均。同一时间桶可以包含多个独立段。每点含 segment、first_sample_at、last_sample_at、sample_count；曲线使用段号断线。网络桶内存在 null，则该方向桶速率为 null；不使用 AVG 忽略 null 后伪造有效值。无样本桶以 null 占位，不向首尾外推或插值。曲线只绘制真实桶点，并提供可访问数据表。

查询限制最近 7 天；所有返回均有固定行数上限。raw 查询采用时间+ID游标与固定 snapshot_id，旧补报不会扰动同一分页快照。旧 Phase 01 表继续保存兼容字段，完整 Dashboard 以 Phase 02 agent_samples 为规范源。

## 保留与清理

所有历史查询在读取时排除 7 天以前样本。单进程后台任务分批清理 agent_samples、disk_metrics、host_metrics，固定表与固定 cutoff，短事务，外键保持启用。每批每表最多 500 行，后台一次最多 5 秒，然后释放资源等下一周期；积压显式可观察，不保证物理删除瞬时完成。禁止 VACUUM 阻塞在线请求。支持默认 dry-run 的运维 CLI 和显式 --apply，不提供公网清理 API。默认每分钟运行，测试／临时性能库可通过设置关闭后台任务。

## 前端

20 秒刷新，可暂停，页面隐藏时暂停；失败保留旧值但显著提示，绝不把旧值标为实时。查询状态和主机详情用 URL hash 路由，无 Token 参数。创建／编辑为独立可访问对话框，轮换／撤销／禁用有明确操作说明和确认。375px 保持无横向溢出；数据表在明确局部区域内滚动。

## 部署边界

本阶段只读 Oracle／Google 环境并编写方案，不安装、不修改服务、防火墙、DNS、WireGuard；等待正式部署验收。已知两台均 AMD64，不能凭云厂商品牌假设 ARM64。
