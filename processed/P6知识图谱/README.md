# P6 清理后正式交付

唯一正式入口为release.json，dataset及真实验证以该文件指向为准。质量流水线当前图谱6763节点、9824条关系，29个线路候选、180个POI、115个具体交通路段、217组时间汇总及265组条件推荐汇总。三个景区均保留。

P6统一负责入库前：线路质量筛选 → 时间汇总 → 条件与推荐汇总 → 实体主题摘要。P5原始抽取记录不变；旧时间派生代码和结果已迁入P6。P5图仅移除了217个时间派生节点及其索引和边，业务节点及关系不变。历史迁移记录已放入仓库外私人资源归档。

- `cleaned-v1/graph.json`：唯一正式图谱；P5的integrated/graph.json是只含抽取层的上游输入，不再直接入库。
- `cleaned-v1/routes.json`：29条整理后线路；广州长隆11、九寨沟8、珠海长隆10。包括24条长游览序列、3条含钟点的专题行程、2条跨园区概要，分类型供后续检索。
- `cleaned-v1/entities.json`、`transport_legs.json`、`conditions.json`、`entity_time_summaries.json`：与正式图一致的对象输出。
- `cleaned-v1/route_decisions.csv/json`：218个原线路对象的保留/排除理由。
- `cleaned-v1/route_fragments_audit.json`：189个排除对象的原始内容、精确原文片段及归属检查；只作审计，不入库。
- `cleaned-v1/scope_cleanup_audit.json`：原片段绑定的处理记录；`cleaning_summary.json`含前后数量、限制及P5冻结哈希。
- 正式dataset目录：导入包、验证、`cleaned_queries.json`真实查询。已真实入库，重复导入不增长，全部节点与边属性读回一致。

P5的不足在于ROUTE粒度混杂，局部安排与交通片段被当作线路；本次在P6独立处理，没有修改或重跑P5。重复访问保留，不以不同实体数筛选；不同文章不合并、不凭共享景点拼路线。

规则：至少六个有序已匹配地点阶段作为长序列；原文明示日程范围的四阶段行程，以及至少五步、三阶段且原文有多处钟点的专题行程设例外。阶段数不是完整性证明。组内地点不推断先后，连续相同地点组只在长度诊断时折算，正式步骤原样保留。两个官方跨园区概要按原文明示站次单独保留。

片段归属同时要求同文章、原文范围包含、地点阶段按序匹配和唯一父候选；本批0条达到可靠归属条件。没有强行拼接，也未宣称完成全面语义去重。29条是可检索候选，不是29条独立人工验证的完整行程。`p6_curation.itinerary_scope`区分日程明示、跨园概要和较长局部序列；`full_day_completeness_verified=false`。

交通路段独立保留。片段专属属性/条件不得扩大为全局知识：原绑定改存`upstream_*`、`scope_status=excluded_route_fragment_scope`，Agent默认推荐时应排除该作用域，原始OBSERVATION仅为证据。未关联实体的条件仍可独立保存。此前时间归类及条件语义疑点未在本轮全面修正。

旧数据库三个项目快照及旧文件先归档后移出。归档位于仓库外自己的资源目录下`P5P6开发与运行归档_20261101/P6二次清理前_20261101`。当前项目数据库只剩清理后dataset。

当前报告为outputs/P6目录中的P1至P6完整版_P6汇总更新版.docx，P6统计与本release一致；P1至P5及P7以后保持原始XML。旧版报告已移入仓库外私人资源归档。HTML点击节点可查看属性、线路步骤、交通端点、时间汇总、条件建议主题及来源证据。报告生成、预览及核对脚本已从tools删除；当前程序运行不依赖这些脚本。

```powershell
python 'src/P6知识图谱/quality_pipeline.py'
python 'src/P6知识图谱/run.py' prepare
# 配置NEO4J_URI/USER/PASSWORD后可执行 import；必须使用清理图。
python 'src/P6知识图谱/run.py' import
python tools/build_p6_extra_html.py
python -m unittest discover -s tests/P6 -v
```


## 新增质量输出

- cleaned-v1/entity_time_summaries.json、time_observation_unions.json：P6从逐条记录重建的217组历史时间汇总，片段作用域及景区信息进入观测，不将失效路线绑定当全局时间。
- cleaned-v1/condition_recommendation_summaries.json：265组无损汇总，覆盖1358条条件规则和112条相关属性，包含257组普通目标、3组未定位、5组片段作用域；不是265条独立推荐。
- cleaned-v1/advice_summary_audit.json：记录覆盖、重复归并、隔离及限制。联合目标会在各对象视图引用，不能把视图条目数当独立规则数。
- cleaned-v1/quality_manifest.json：四阶段完成状态、图及上游输入哈希；缺少此清单或哈希不一致禁止入库。
- quality-stages/route-filtered/：只用于审计和重建的线路筛选中间结果，不能直接入库。
- 旧时间迁移结果、三个旧入库快照和themes-20261101-v1试跑结果已移到私人资源目录，不作为当前输入。

本轮另对257个有实体目标的景区/实体汇总组执行真实deepseek-flash主题归纳，生成548条标签和自然语言摘要，附着在249个唯一POI、交通方式或路段实体ID的advice_themes属性中；共享交通方式会在不同景区上下文分别归纳。未定位的3组及片段作用域的5组不做LLM归属。结果位于llm_advice/runs/themes-20261101-v2/，调用日志保存在仓库外资源归档。输出和来源覆盖已校验，语义正确性未全量独立人工标注。Word及章节Markdown已于2026-11-02同步当前正式图及汇总流程。

## 当前保留目录

- cleaned-v1/：当前最终图谱、对象输出及清理依据。
- p6-quality-themes-v1-1bae9157a9a3/：release指定的唯一正式入库快照。
- llm_advice/：仅保留当前v2主题结果及其版本索引，供离线重建。
- quality-stages/route-filtered/：当前流水线使用的线路筛选中间结果，也是现有回归检查的输入；属于当前版本依赖，继续保留。

2026-11-02归档位置：`D:\文档\大三上\数据挖掘\实验一\小组仓库外我自己的资源\P6处理结果旧版归档_20261102`。共移动5个目录、21个文件，移动清单及SHA-256随归档保存。当前正式图、入库包及主题结果内容未修改。
