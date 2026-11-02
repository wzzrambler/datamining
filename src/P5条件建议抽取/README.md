# P5/P5+ 条件建议抽取源码

Python + OpenAI SDK，自建非流式工具循环，模型deepseek-flash，单次上下文上限30000 tokens。模型参数在config.json；Key与服务地址仅从DEEPSEEK_API_KEY、DEEPSEEK_BASE_URL环境读取，不写入仓库。

## 源码布局

- run.py：prepare / run / export入口。
- p5_agent/packages.py：关联P1/P2/P3，按景区组包与覆盖统计。
- p5_agent/contracts.py、host.py：六个工具的Schema、ID/类型/来源作用域校验、错误修复、撤回依赖与结束检查。
- p5_agent/runner.py、budget.py：非流式工具循环、预算、台账压缩和断点；不执行模型生成代码。
- p5_agent/export.py：逐条抽取与确定性关联输出，保留原始时间属性；不再生成跨文章汇总。
- 时间汇总代码已迁至src/P6知识图谱/p6_quality/times.py，相关测试迁至tests/P6。线路筛选和条件推荐汇总也统一由P6负责。
- assets/：当前系统提示词、模型工具说明、宿主工具及上下文Schema。
- condition_terms.json：选段触发词，不是事实判定器。

## 运行

```powershell
python src/P5条件建议抽取/run.py prepare
# 真模型调用：选择新run-id；需要环境Key，会产生API用量。
python src/P5条件建议抽取/run.py run --run-id new-smoke --limit 1
python tools/P5/run_batch.py --run-id new-full --workers 6
# 只重建现有正式结果，无网络、无Key、无外部日志依赖。
python src/P5条件建议抽取/run.py export --run-id full-final-20261101
python -m unittest discover -s tests/P5 -v
```

处理顺序由提示词引导：条件→地点→交通→线路→结束。交通必须有两个已注册地点ID；条件可无目标。每工具保存断点，截断回复整批不执行；同一运行的输入/配置/代码变化会拒绝续跑，应使用新run-id。恢复工具tools/P5/recover_packages.py保留失败日志并核对片段覆盖。

正式版本、输出字段及课程映射表见processed/P5条件建议抽取/README.md；章节草稿见outputs/P5。老师任务映射291条，扩展对象1358条；二者统计口径不同。当前原文支持精度为输入包，不能宣称逐条事实已人工确认。历史提示词、试跑和开发日志已移至仓库外。
