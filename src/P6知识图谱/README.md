# P6 入库前质量流水线与图谱导入

P5只保存逐条抽取与关联。P6执行四个阶段：线路质量筛选 → 历史时间汇总 → 条件与推荐汇总 → 实体主题摘要。正式入口为processed/P6知识图谱/release.json；最终图谱6763节点、9824关系，保留29个线路候选、217组时间汇总，含265组条件推荐汇总。另对257个实体/景区汇总组调用deepseek-flash，生成548条主题标签与自然语言摘要，附着于249个唯一实体ID；Neo4j中以advice_themes_json保存，可按实体查询。

```powershell
python src/P6知识图谱/quality_pipeline.py
python src/P6知识图谱/run.py prepare
# 配置NEO4J_URI/USER/PASSWORD后执行import
python src/P6知识图谱/run.py import
python tools/build_p6_extra_html.py
python -m unittest discover -s tests/P6 -v
```

- clean_routes.py：线路筛选，输出quality-stages/route-filtered中间材料；它不能单独生成可入库版本。
- p6_quality/times.py：从P5迁入，依据筛选后FACT/RULE重建时间集合及区间并集，保留历史性、景区和片段作用域。
- p6_quality/advice.py：从全部条件规则及112条相关属性生成无损归集；保留条件、语气、联合目标、原始记录ID和来源。只合并明确重复，尚未进行LLM同义归并或全面冲突检测。
- quality_pipeline.py：统一调度，重建派生节点及边，输出quality_manifest.json；读入已验证的LLM结果时只做离线绑定，不请求模型。
- p6_quality/llm_themes.py与summarize_advice_llm.py：按实体/景区组织上下文，调用模型生成主题摘要并校验引用覆盖；提供plan/run入口和可续跑检查点。
- run.py：只接收四阶段完成且图谱与输入哈希匹配的最终图；含ADVICE_SUMMARY、HAS_ADVICE_SUMMARY、DERIVED_FROM，保留既有类型。

未定位建议按景区作为“原文来自该景区”的集合保存，不代表对全景区适用。片段作用域汇总单独隔离，默认推荐排除scope_status非空组；原始依据仍可追溯。没有以来源频次替代置信度。

真实入库使用(dataset,id)唯一约束；嵌套值存JSON并完整读回核验。首次替换旧版本用tools/P6/publish_cleaned.py的--replace-dataset，必须等于当前release指向版本，先备份、验证新图，再移出该旧版本；其他数据库内容不操作。重复导入使用run.py。

HTML从同一正式图生成，节点侧栏先展示时间汇总，再展示条件建议主题摘要，并保留原始记录。2026-11-02已重新生成P6汇总更新版Word及章节Markdown，统计同步release的6763节点、9824关系；6498/7749仅为加入条件推荐汇总前的中间版本。
