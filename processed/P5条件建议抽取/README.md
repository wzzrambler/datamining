# P5 正式处理结果

唯一正式版本：`runs/full-final-20261101`。覆盖2226原始包：2225直接完成，1包以4片段恢复，共2229有效包。保留3751条原始业务记录（3741有效），整合1358条件/建议、375地点属性、115交通路段及218线路对象。

- 老师任务：[表格/条件建议映射表.csv](表格/条件建议映射表.csv)，291个整合后的条件建议/限制；JSON版本同目录。
- 扩展结果：[表格/全部条件与建议.csv](表格/全部条件与建议.csv)，1358对象；来源证据索引同目录。
- `runs/full-final-20261101/integrated/`：实体、线路、路段、条件、逐条时间属性、观测和P6输入graph.json；不包含TIME_SUMMARY节点。
- `raw_observations.json`：包含有效/撤回标志与来源的原始识别层；`input_manifest.json`：输入块、来源、覆盖及注册表；`summary.json`：真实运行统计；`aggregation_manifest.json`：失败包替换与覆盖依据。
- `release_integrity.json`：正式结构校验哈希；根部`release.json`为版本入口。

离线重建：`python src/P5条件建议抽取/run.py export --run-id full-final-20261101`。不需要Key、外部请求日志或Neo4j，不调用模型；保留历史真实用量。已固定节点/边输出顺序，跨进程重建字节一致。

开发过程的完整HTTP回复、旧版试跑、重复检查点及调试审阅已移出仓库，正式结果的重建和课程章节不依赖它们。时间文本是历史观测，语义待复核项详见outputs/P5/校验；没有人工准确率保证。

2026-11-01职责迁移：时间汇总和时间并集结果已移至processed/P6知识图谱/migration/time-from-P5作为历史快照；当前正式结果由P6的quality_pipeline.py重建。P5原始抽取记录及非派生业务节点/关系保持一致，图中仅移除217个旧时间汇总节点、关联边及汇总索引。迁移证明见processed/P6知识图谱/migration/ownership_migration.json。历史实验用量不变，未重跑模型。
