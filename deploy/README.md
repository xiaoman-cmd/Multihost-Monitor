# 开发运行

在仓库根目录配置 `.env` 后执行 `./deploy/dev.sh`。脚本安装锁定依赖、构建前端、应用迁移，然后由唯一的 Uvicorn 进程提供 API 和静态页面。没有 reload 子进程、后台任务或外部队列。默认仅监听 `127.0.0.1:8000`。

这不是生产部署方案。生产应使用 HTTPS 反向代理、`MHM_COOKIE_SECURE=true`、正确的 `MHM_ORIGIN`、单 worker，并限制数据库和配置文件权限。代理不得让公网客户端控制真实来源地址；当前限流按照直接连接地址计数，反向代理后会共享配额。应用关闭 Uvicorn proxy headers 信任。
