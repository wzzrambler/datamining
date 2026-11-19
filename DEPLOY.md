# P7 部署与验收

本机安装、数据库导入、Vue 构建和回归测试见 [P7 使用说明](src/P7旅游规划Agent/README.md)。服务器镜像、Compose 启动参数和回滚流程见 [P7 部署说明](src/P7旅游规划Agent/deploy/README.md)。

已验收的 [行迹公网入口](http://8.163.104.139:8090) 运行在 Docker Compose 中。前端与 API 同源，模型密钥从环境读取。浏览器会话按 Cookie 隔离。
