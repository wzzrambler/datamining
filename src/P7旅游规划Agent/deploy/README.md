# P7 服务器部署

本方案使用一个 API 容器和一个 Nginx 容器，复用现有 PostgreSQL。Nginx 同源提供静态页面和 API。Compose 默认监听 `127.0.0.1:8017`，当前服务器发布监听 `0.0.0.0:8090`。API 保持单 worker，SSE 在反代层关闭缓冲。

## 构建发布镜像

从仓库根目录构建前端后运行 staging 脚本。脚本输出临时构建目录及发布 ID，由前端产物、API 源码和 P6 导入文件共同计算。P6 知识使用正式发布 JSON，运行配置由目标服务器提供。

```powershell
npm --prefix src/P7旅游规划Agent/web ci
npm --prefix src/P7旅游规划Agent/web run build
python src/P7旅游规划Agent/deploy/stage.py
```

进入 staging 脚本输出的目录构建镜像，把示例中的 `RELEASE` 替换为实际发布 ID：

```powershell
docker build --platform linux/amd64 -f src/P7旅游规划Agent/deploy/Dockerfile.api -t p7-1-api:RELEASE .
docker build --platform linux/amd64 -f src/P7旅游规划Agent/deploy/Dockerfile.web -t p7-1-web:RELEASE .
docker image save -o p7_1_images.tar p7-1-api:RELEASE p7-1-web:RELEASE
```

## 初始化与启动

将已校验的镜像归档传到服务器并执行 `docker load -i p7_1_images.tar`。发布目录放置对应的 `compose.yaml` 和 `compose.env`：

```dotenv
P7_1_RELEASE=RELEASE
P7_1_SHARED_DIR=/opt/p7_1/shared
P7_1_WEB_PORT=8017
P7_1_WEB_BIND=127.0.0.1
P7_1_BROWSER_SESSIONS=0
P7_1_DB_NETWORK=study-agent_default
```

先在 PostgreSQL 创建独立的 `p7_1` 库及迁移角色、`p7_1_runtime` 角色。数据库内网主机名为 PostgreSQL 容器在指定网络中的别名。共享目录的 `migration.env` 只配置 `P7_1_MIGRATION_URL`，`runtime.env` 配置 `P7_1_DATABASE_URL` 与 `DEEPSEEK_API_KEY`，两个文件仅授权部署管理员读取。

```sh
docker compose -p p7_1 --env-file compose.env --profile setup run --rm --no-deps migrate
docker compose -p p7_1 --env-file compose.env up -d --pull never --wait api web
curl -fsS http://127.0.0.1:8017/api/v1/status
```

PC 侧通过已授权的 SSH 密钥保持端口转发。示例使用本机 `18117`，服务器侧为 `8017`。

```powershell
ssh -i ~/.ssh/study_agent_deploy -N -L 127.0.0.1:18117:127.0.0.1:8017 root@8.163.104.139
```

打开 [行迹服务器版](http://127.0.0.1:18117)。确认实际模型能调用工具并保存行程，再验证修改、历史版本与 SSE 续读。

## 公网验收入口

在 `compose.env` 中设置以下参数，重新执行 Compose 启动命令：

```dotenv
P7_1_WEB_PORT=8090
P7_1_WEB_BIND=0.0.0.0
P7_1_BROWSER_SESSIONS=1
```

打开 [行迹公网版](http://8.163.104.139:8090)。当前使用 HTTP。聊天和行程归属由浏览器 Cookie 标识，清除站点数据或更换浏览器后会进入新的会话空间。此前 SSH 验收时的会话仍存于数据库，只有关闭浏览器会话隔离的私有部署配置可以访问原会话空间。部署完成后检查 `/api/v1/status`、真实对话、行程保存和编辑，以及两个浏览器之间的会话隔离。

## 更新与回滚

保留每版镜像、Compose 配置和发布 ID。切换版本前确认当前回合已结束，并在涉及数据库变更前备份 `p7_1`。回滚时从上一版发布目录以同一 Compose 项目名启动；如果迁移改动了旧版不兼容的表结构，先在隔离库验证备份恢复。
