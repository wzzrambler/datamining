# P7 旅游规划 Agent（ReAct）

基于项目 2/3/5/6 的产物做旅游规划：一句话需求（景区、人群、天数），
Agent 循环调用确定性工具取路线、景点、条件建议，按人群裁剪并按天拆分后输出行程。

## 文件

| 文件 | 作用 |
|---|---|
| `p7_agent.py` | Agent 全部逻辑：槽位解析、5 个工具、ReAct 循环、规则/LLM 两套策略 |
| `api.py` | FastAPI 接口层：参数校验、限流、并发控制、SSE 推送，不复制业务规则 |
| `web/` | 前端单页（HTML/CSS/JS，无框架无构建） |
| 仓库根 `tests/P7/` | 26 条测试：工具契约、循环出口、容量拆分、Web 接口 |

## 数据来源

只读不写。启动时加载：

- `processed/P2实体识别/entities.json` —— 景点提及（`search_spots` 的候选池）
- `processed/P5条件建议抽取/runs/<ACTIVE_RUN>/…` —— 观察、路线
- `processed/P6知识图谱/cleaned-v1/{routes,conditions}.json` —— 29 条路线、1269+ 条条件建议

不连 Neo4j：JSON 一定在，图数据库在现场不一定能起来。

## 图谱检索范围

规划只读取实体关联的两类汇总（`HAS_TIME_SUMMARY` / `HAS_ADVICE_SUMMARY`）与实体属性
`advice_themes`；路线取 P6 清洗后的正式线路。时间、班次、票价与政策都来自历史资料，
未记录的时间不代表关闭，未核实的旧信息不能表述成当前实时规则；
一般 `FACT` 与单条 `CONDITIONAL_RULE` 不作为规划输入。

## 命令行

```bash
python src/P7旅游规划Agent/p7_agent.py                      # 跑七个场景，写 outputs/P7/
python src/P7旅游规划Agent/p7_agent.py --ask "带老人去九寨沟一天"
OPENAI_API_KEY=... OPENAI_BASE_URL=https://api.deepseek.com/v1 \
OPENAI_MODEL=deepseek-chat \
python src/P7旅游规划Agent/p7_agent.py --llm --ask "带6岁孩子去广州长隆"
```

不配 `OPENAI_API_KEY` 自动退回规则策略：同一循环、同一工具，结果可重复。

## Web

```bash
pip install fastapi uvicorn
python src/P7旅游规划Agent/api.py               # http://127.0.0.1:8000
```

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/health` | GET | 状态、数据规模、是否配置模型 |
| `/api/scenes` | GET | 三个景区与路线数 |
| `/api/examples` | GET | 示例问句 |
| `/api/plan` | POST | 同步规划，返回完整结果 |
| `/api/plan/stream` | GET | SSE：meta → slots → turn×n → result |

环境变量：`P7_HOST` / `P7_PORT`、`P7_MAX_CONCURRENCY`（默认 4）、
`P7_RATE_LIMIT` / `P7_RATE_WINDOW`（默认 60 次 / 60 秒，按 IP）、
`P7_CORS_ORIGINS`（默认 `*`，部署时改成前端域名）、
`P7_REQUEST_TIMEOUT`（默认 150 秒）、
`P7_ACCESS_TOKEN`（默认空=不启用；公网部署必须设置，所有接口要求
`Authorization: Bearer <token>` 或 `?token=`，前端会弹窗收集并存 localStorage）。

限流与并发计数是**单进程内存态**：uvicorn 保持单 worker 时有效，
多 worker 部署需换成 Redis 等外置存储。密钥只放服务端环境变量。

## 行为约定

- 模型不读图谱原文，工具返回压缩摘要；错也只会错在参数上。
- 参数按函数签名过滤，多余键丢弃并记入 `diagnostics`。
- 同一调用连续失败两次强制收尾。
- 步数超过 `MAX_STEPS_PER_DAY=12` 时按实际天数拆分并说明；两日以上路线由一日路线按原文顺序切分。
- `get_tips` 按人群排除不合的提醒（给情侣不再出现"带小孩别错过水战"）。
- 状态五种：`ok` / `partial`（有提醒）/ `need_info` / `no_data` / `conflict`。

## 已知限制

- 29 条路线仅 9 条带时刻、两日路线 1 条；两天以上只能由一日路线切分，没有过夜语义。
- 12 步/天是启发式阈值，未按体力数据标定。
- 图谱发布 `verified=false`：本机没有 Neo4j 实例，导入包已生成，回读验证待联网库后执行。
