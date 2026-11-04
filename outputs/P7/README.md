# P7 旅游规划 Agent 展示产物

| 文件 | 内容 |
|---|---|
| `agent_runs.json` | 七个场景的规则策略完整记录：每轮的思考、动作、参数、观察与最终答案 |
| `agent_runs_llm.json` | 同一组场景的真模型记录，用于两条策略的对照 |
| `agent_demo.txt` / `agent_llm_demo.txt` / `agent_llm_all.txt` | 上述记录的排版版本，直接阅读用 |
| `行迹-离线预览.html` | 单文件预览：七个场景的真实结果内联，双击打开，不依赖后端与网络 |
| `figures/p7_react.png` | ReAct 循环结构与七个场景的结果 |
| `figures/web-首页.png` / `web-结果-浅色.png` / `web-结果-深色.png` | Web 界面截图 |

重新生成：

```bash
python src/P7旅游规划Agent/p7_agent.py        # 七场景记录与图
python tools/P7/build_offline.py              # 离线单页与 web/offline.json
```

起服务见 `src/P7旅游规划Agent/README.md`：本机 `serve.py`，公网 `serve.py --public`。
