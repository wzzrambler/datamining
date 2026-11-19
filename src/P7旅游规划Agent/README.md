# P7 行迹旅游规划 Agent

Vue 3 + Vite、FastAPI、PostgreSQL 和 DeepSeek Flash 的多轮旅游规划应用。P6 知识数据与业务数据分别落在 `p6_kg` 和 `p7_app`。

## 本机启动

在 `src/P7旅游规划Agent` 中配置 `P7_1_DATABASE_URL`、可选 `P7_1_MIGRATION_URL` 和 `DEEPSEEK_API_KEY`。本地连接配置也可放在已忽略的 `.env.local`，使用 `KEY=value`。PostgreSQL URL 采用 `postgresql+psycopg://`。应用角色需要读取 `p6_kg` 和读写 `p7_app`，迁移角色需要建表及导入权限。

```powershell
python -m pip install -r requirements.txt
python -m alembic upgrade head
python -m p7.import_p6
python run.py
```

使用本机已有的 `p7_1_runtime` 角色时，可在初次导入加 `--grant-local` 应用上述权限。导入以 P6 发布清单与质量哈希为准。同一版本无变化时重复执行不会新增记录。

在另一个终端启动 Vue：

```powershell
cd web
npm ci
npm run build
npm run preview
```

访问 [http://127.0.0.1:5177](http://127.0.0.1:5177)。前端默认代理到 8017 的 API，使用 `P7_1_API_PROXY` 可改代理地址。直接单独部署构建时可通过 `VITE_API_BASE` 选择 API 前缀。API 采用单进程运行回合执行器，浏览器断开 SSE 之后可以凭回合和事件序号恢复。

## 范围与验证

- 支持多轮澄清、P6 路线排序与摘要、景点及来源、创建行程以及聊天或表单修改。历史消息可打开当时的行程版本。
- DeepSeek 原生工具调用。读取 `usage.prompt_tokens` 作为压缩依据，旧回合在实测超过 100,000 后压缩，最新一轮原消息与工具配对完整保留。每条新用户消息最多 10 次决策和 30 次工具调用。
- 模型请求的 `messages[]` 在两次压缩之间按原顺序追加。会话资料在用户消息入库时固定下来。当本轮已执行至少 8 次决策或 20 次工具调用，新增的工具结果通过 `loop_control` 告知剩余额度。工具定义保持固定，由后端拦截超额调用。输入 token 用量供后端判断压缩。
- 压缩时，在旧轮次的原有消息前缀末尾追加临时用户指令，请模型提取需求、证据、行程和待确认项。校验摘要后，将它作为独立的内部消息放在固定 `system` 和最新轮次之间。摘要请求不占用 ReAct 决策和工具额度。
- P6 保存历史记录及来源，行程遇到交通或时间证据不足时标记待确认。

```powershell
python -m unittest discover -s tests -v
$env:P7_1_RUN_DB_TESTS='1'
python -m unittest discover -s tests -v
```

数据库集成测试使用正式 P6 数据和已迁移的业务表。持久化的回合测试会创建标题以「P7_1 回归」开头的测试会话。
