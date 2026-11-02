# P3全自动路线层级挖掘

当前实现参照旧版的全文规则扫描，直接读取P1正文和P2正式v2。没有预设文档ID、段号、原句、日序或比较配对，也不读取历史选段文件或既有路线输出。

## 运行

在仓库根目录执行：

```powershell
python "src/P3路线层级挖掘/run.py"
python -m unittest discover -s tests/P3 -v
```

Python 3.11及以上；核心仅标准库，Matplotlib绘制PNG/SVG。--no-plots跳过绘图。无需模型接口，不联网。输入固定为P1 documents.json、P2 entities.json及paragraph_sources.json；这里固定的是阶段输入路径，路线位置全部在运行中计算。P2须标记v2且其输入和来源哈希与P1对应。

## 自动处理

1. discovery.py遍历所有文档全部段落；按正文、每张图片和评论的来源边界重置状态。
2. 自动识别短路线标题、非路线主题、Day/第几天、时间标题；确定路线和时间作用范围。
3. 识别第X站、带钟点编号、箭头链、共同时间移动段、明确依次游玩列表及有顺序连接词和P2目的地的叙述。
4. 用P2 mention_id关联POI、交通和时间。活动保留字面动作；不确定内容保存原句和未决项。
5. 同来源不同格式分别形成候选，正文优先于OCR；按相同日序/范围/粒度与POI序列相似度自动抑制重复摘要。日志保留被抑制内容。
6. 从P2的景区主体类型、标题和出入口名称自动推断路线范围/粒度；不明确则标未知，不强制配对。
7. 遍历全部官方和游客路线组合；同景区、范围、粒度且至少两个共同游览POI时自动比较，无固定文档ID。图表样例与对比排名也自动选择。

编号列表缺少路线语境时不当路线；乱序编号可能是地图索引，拒绝建立访问顺序。提示、否定、外部交通、无实体支撑和不完整OCR链保留拒绝日志。规则不能识别所有隐含路线，全部自动不等于语义完全正确。

## 模块

- discovery.py：全量发现、状态机、候选接纳、自动去重、范围判断和配对。
- parser.py：运行时证据切片、步骤、活动/交通/时间、层级和指标。
- pipeline.py：输入对应检查、结构验证、诊断、CSV/JSON、报告和图表。
- run.py：命令行入口。正式流程没有定位配置。

## 字段

routes.json记录route_id、doc_id、format、nature、scope、granularity、steps、order_relations和hierarchy。step原文位置是算法输出，不是预设输入。

poi_refs为核心片段所有P2提及；route_poi_refs限定访问节点，排除括号里的可选介绍和明确未到访提及。p3_normalized_links是同景区唯一标准名后缀的自动链接（完整节点、至少4字），单独记录目标P2提及；不修改P2。未知标准名保持原写法，不把父区域和子景点强制合并。

nature为official_recommendation、visitor_recommendation、visitor_actual_claim或visitor_unspecified。actual_claim仅表示文本触发自述规则，未独立核实。review_status统一automatic_unreviewed，不冒充人工或AI逐条验收。

时段优先用原文标题，其次局部时段或钟点；缺失为未说明。day=null表示日序未知。编号重置到1但日序已改变时，保留两天的层级；同一天编号乱序不按访问顺序输出。order_group表示时段内无序兄弟项，index仅为显示索引。

移动段总时段不分摊给景点；duration保留原文及语境角色，不用时刻差补造停留时长。时段步骤数不能当作分钟数。

覆盖度=共同游览POI/官方游览POI；Jaccard=交集/并集；顺序指标=共同POI序列LCS/max长度。配套与景区外POI排除。无序组/未决分支不计算顺序指标，非连续重访保留，只折叠相邻重复。

## 输出与核对

processed/P3路线层级挖掘保存routes.json、route_steps.csv、candidate_inventory.json、route_compare.json、summary.json、validation.json、diagnostics.json和run_manifest.json。candidate_inventory含所有扫描信号、接纳/拒绝/重复抑制及理由，不识别不等于无路线。

outputs/P3保存可读路线、自动生成报告、自动质量核对记录和图表。测试包含更换ID、插入段落、移动段号、新输入的完整管线、来源边界、时间作用域、否定、OCR和指标。输入哈希记录及重复运行检查提供复现证据，不报告人工P/R/F1。

旧定位配置首版已撤回；archive/P3_已撤回的定位配置首版.zip仅供回滚，不参与任何当前运行。旧组程序也仅作为参考。
