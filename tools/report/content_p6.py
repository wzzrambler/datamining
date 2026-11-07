# -*- coding: utf-8 -*-
"""第 6 章：知识图谱。"""
import json
from collections import Counter
from pathlib import Path

from facts import FACTS as F

P6 = F['p6']
ROOT = Path(__file__).resolve().parents[2]
CLEAN = ROOT / 'processed/P6知识图谱/cleaned-v1'
GRAPH = json.loads((CLEAN / 'graph.json').read_text(encoding='utf-8'))
CLEANING = json.loads((CLEAN / 'cleaning_summary.json').read_text(encoding='utf-8'))
TIMES = json.loads((CLEAN / 'entity_time_summaries.json').read_text(encoding='utf-8'))
ADVICE = json.loads((CLEAN / 'condition_recommendation_summaries.json').read_text(encoding='utf-8'))
MANIFEST = json.loads((CLEAN / 'quality_manifest.json').read_text(encoding='utf-8'))
NODES = Counter(n['label'] for n in GRAPH['nodes'])
EDGES = Counter(e['type'] for e in GRAPH['edges'])
# 主题数字以 llm_advice/release.json 为准；publish_cleaned --apply 写的图发布清单不含这些字段
_ADVICE_REL = json.loads((ROOT / 'processed/P6知识图谱/llm_advice/release.json').read_text(encoding='utf-8'))
THEMES = _ADVICE_REL.get('themes', 0)
GROUPS = _ADVICE_REL.get('entities', 0)
ENTITIES = _ADVICE_REL.get('unique_entity_ids', 0)
# 时间汇总也有挂在入口、交通上的，"景点有没有时间"必须按 POI 本身数
POI_TOTAL = sum(1 for n in GRAPH['nodes'] if n['label'] == 'POI')
POI_WITH_TIME = sum(1 for n in GRAPH['nodes']
                    if n['label'] == 'POI' and n['id'] in {t['target_id'] for t in TIMES})
ENTITIES_WITH_TIME = len({t['target_id'] for t in TIMES})
CHUNKED = (GRAPH['p6_quality'].get('llm_entity_themes') or {}).get('chunked_groups') or []
CHUNK_ITEMS = CHUNKED[0]['items'] if CHUNKED else 0
TARGETED = sum(1 for a in ADVICE if a['target_id'])
QUARANTINED = sum(1 for a in ADVICE if a['scope_status'])
LABEL_CN = {'POI': '景点/设施', 'ROUTE': '线路', 'ROUTE_STEP': '线路步骤', 'TRANSPORT': '交通方式',
            'TRANSPORT_LEG': '交通路段', 'CONDITIONAL_RULE': '条件规则', 'FACT': '事实',
            'OBSERVATION': '原始观察', 'TIME_SUMMARY': '时间汇总', 'ADVICE_SUMMARY': '建议汇总'}
EDGE_CN = {'HAS_STEP': '线路含步骤', 'VISITS': '步骤到访景点', 'PRECEDES': '步骤先后',
           'FROM': '路段起点', 'TO': '路段终点', 'USES_MODE': '路段用交通方式', 'USES_LEG': '步骤用路段',
           'HAS_FACT': '实体挂事实', 'APPLIES_TO': '规则作用于', 'PART_OF': '从属关系',
           'SUPPORTED_BY': '汇总挂原始观察', 'HAS_TIME_SUMMARY': '实体挂时间汇总',
           'HAS_ADVICE_SUMMARY': '实体挂建议汇总', 'DERIVED_FROM': '汇总来自记录',
           'MENTIONS_MODE': '事实提到交通方式'}
DECISION_CN = {'long_touring_sequence': '完整游览序列', 'timed_thematic_itinerary': '带时刻的主题行程',
               'multi_park_overview': '跨园区概览', 'single_stage': '只有单点',
               'local_fragment': '局部片段', 'transport_fragment': '交通片段',
               'ambiguous_alternatives': '并列备选，顺序不明', 'unresolved_fragment': '无法判定'}
KEPT = CLEANING['decisions']['long_touring_sequence'] + CLEANING['decisions']['timed_thematic_itinerary'] \
    + CLEANING['decisions']['multi_park_overview']

CONTENT = {}

CONTENT['6.1'] = [
    ('p', '上游五章产出五类结果：清洗后的正文（P1）、实体提及（P2）、候选路线（P3）、指代关联（P4）、条件与事实（P5）。各环节依靠 ID 衔接，不依赖文本相似度。entity_id 由类型、景区、来源类别与规范名共同哈希生成，与实体出现在哪篇文档、第几个字符无关，因此同一处"熊猫村"在 P2、P3、P5 中指向同一节点。重跑验证表明：P1 重新清洗后字符偏移全部变化，实体 ID 保持不变，仅提及位置（mention_id）随之改变。来源关联另有一条原则：不因来源类别不同而重复建立同一注册实体，也不将官方来源直接等同于当前仍有效的事实——官方记录的时间同样只是历史数据。'),
    ('p', '融合顺序固定为四步，每步均设独立验收：先执行线路清理（将 P5 的 %d 个线路候选收束为 %d 条正式线路），再汇总时间（同一实体同一属性的多条时刻合成为区间并集），再汇总条件建议（同一实体的多条建议按目标合并），最后挂接 LLM 主题（将条目归纳为若干自然语言主题）。顺序不可调换——时间汇总需读取清理后的线路，主题生成需读取汇总后的建议。' % (
              CLEANING['before']['routes'], len([n for n in GRAPH['nodes'] if n['label'] == 'ROUTE']))),
    ('p', '外来数据一律不新增 ID。P5 中出现过未注册地点（"沟口""诺日朗换乘"一类），处理方式为保留原步骤文字、不建立节点，也不强行并入名称相近的景点。该处理会使部分步骤缺少 VISITS 边，但优于错误关联——第 3 章"宁可漏拼一条，不可拼错一条"的原则在此同样适用。'),
]

CONTENT['6.2'] = [
    ('p', '%d 个线路候选中仅 %d 条进入正式图。其余 %d 条并非"错误"，而是"不足以构成线路"：%d 条仅含单点（"南门进，看熊猫"），%d 条为段落中的局部序列，%d 条为纯交通片段（"坐大巴到沟口"），另有 %d 条为并列备选、顺序不明。上述记录全部写入 route_fragments_audit.json，每条附拒绝理由与原文证据，未作静默丢弃。' % (
              CLEANING['before']['routes'], CLEANING['after']['routes'], CLEANING['fragments'],
              CLEANING['decisions']['single_stage'], CLEANING['decisions']['local_fragment'],
              CLEANING['decisions']['transport_fragment'],
              CLEANING['decisions']['ambiguous_alternatives'] + CLEANING['decisions']['unresolved_fragment'])),
    ('tbl', '表6-1  线路候选的判定分布',
     ['判定', '条数', '是否进正式图', '判定依据'],
     [[DECISION_CN[k], str(v), '是' if k in ('long_touring_sequence', 'timed_thematic_itinerary',
                                            'multi_park_overview') else '否',
       {'long_touring_sequence': '步骤 ≥5 且顺序明确、证据连续',
        'timed_thematic_itinerary': '带时刻分段，可还原一日行程',
        'multi_park_overview': '原文明确记载两园先后',
        'single_stage': '不足 2 个可排序站点',
        'local_fragment': '跨度只覆盖段落的一部分',
        'transport_fragment': '只有交通，没有游览点',
        'ambiguous_alternatives': '两条以上并列，顺序无证据',
        'unresolved_fragment': '证据互相矛盾或缺失'}[k]]
      for k, v in sorted(CLEANING['decisions'].items(), key=lambda kv: -kv[1])]
     + [['合计', str(CLEANING['before']['routes']), '%d 条' % CLEANING['after']['routes'], '—']],
     [3.6, 1.8, 2.6, 6.4]),
    ('p', '噪声中最难处理的一类并非碎片，而是"同名不同位置"。三篇游记均写有"熊猫馆"，广州与珠海的熊猫馆属不同地点，程序依据景区码加以区分；同一景区内两个同名的"餐厅"则无法区分，只能保留两条 mention、共用一个 entity_id。此为已知欠账，体现在 P4 的"类型不符"与 P5 的"目标落不准"两处统计中。'),
    ('p', '另一类为"父级挂错"：%d 条事实的 attribute 是 parent_park，本意是表达"该景点属于哪个园区"，抽取结果有时变为"该景点位于园区内"，关系方向相反。此类未作自动修正，仅在质量清单中记录，使用时须回查原文。' % len(
              [f for f in (n['properties'] for n in GRAPH['nodes'] if n['label'] == 'FACT')
               if f.get('attribute') == 'parent_park'])),
]

CONTENT['6.3'] = [
    ('p', 'Schema 定义 10 个节点标签与 15 种关系，原则只有一条：一个节点仅表示一类对象，一条关系仅表示一个方向的断言。例如线路与步骤分为两层（ROUTE 与 ROUTE_STEP），因为线路属性（性质、粒度、来源）与步骤属性（到访景点、时间、交通）不属于同一层级；若合并为一层，查询"这条线路经过几个景点"须先遍历全部步骤属性。'),
    ('tbl', '表6-2  节点标签',
     ['标签', '含义', '本次节点数', '主要属性'],
     [[LABEL_CN[k], k, str(v),
       {'POI': 'name / role / scenic_code / advice_themes',
        'ROUTE': 'nature / scope / granularity / p6_curation',
        'ROUTE_STEP': 'index / time / transport / evidence',
        'TRANSPORT': 'name / kind', 'TRANSPORT_LEG': 'from / to / duration',
        'CONDITIONAL_RULE': 'condition / consequence_text / force',
        'FACT': 'attribute / value_text / modality / condition',
        'OBSERVATION': 'tool_name / provenance / package_key',
        'TIME_SUMMARY': 'attribute / text_union / interval_unions',
        'ADVICE_SUMMARY': 'target_id / items / category_counts'}[k]]
      for k, v in NODES.most_common()],
     [2.8, 3.4, 2.2, 6.2]),
    ('tbl', '表6-3  关系类型',
     ['关系', '从 → 到', '条数'],
     [[k, {'HAS_STEP': '线路 → 步骤', 'VISITS': '步骤 → 景点', 'PRECEDES': '步骤 → 下一步',
           'FROM': '路段 → 起点', 'TO': '路段 → 终点', 'USES_MODE': '路段 → 交通方式',
           'USES_LEG': '步骤 → 路段', 'HAS_FACT': '实体 → 事实', 'APPLIES_TO': '规则 → 作用对象',
           'PART_OF': '景点 → 上级园区', 'SUPPORTED_BY': '时间汇总 → 原始观察',
           'HAS_TIME_SUMMARY': '实体 → 时间汇总', 'HAS_ADVICE_SUMMARY': '实体 → 建议汇总',
           'DERIVED_FROM': '建议汇总 → 条件规则', 'MENTIONS_MODE': '事实 → 交通方式'}[k], str(v)]
      for k, v in EDGES.most_common()],
     [4.0, 6.4, 2.0]),
    ('p', '属性存储存在一项限制：Neo4j 属性不能是嵌套对象或对象数组。处理方式是将完整负载序列化为 payload_json 保存一份，同时把可展开的标量字段（名称、景区、类别）单独存为可查询属性，列表元素类型混杂时再拆分为 xxx_json。该设计既支持 Cypher 直接查询，也可取回原始数据重算哈希——导入回读校验即依赖 payload_json。'),
]

CONTENT['6.4'] = [
    ('p', '构建分四步执行，均要求幂等：相同输入运行两次，产物字节一致。线路清理与四步汇总各有 manifest，记录输入哈希；上游一经改动，下游即拒绝运行，不会出现"新数据配旧参数"的不一致结果，须显式刷新指纹后方可继续。'),
    ('tbl', '表6-4  构建四步与产物',
     ['步骤', '产物', '本次规模'],
     [['线路清理', 'graph.json / routes.json / route_fragments_audit.json', '%d 条线路、%d 个片段被剔除' % (
         CLEANING['after']['routes'], CLEANING['fragments'])],
      ['时间汇总', 'entity_time_summaries.json', '%d 个实体、%d 条汇总' % (POI_WITH_TIME, len(TIMES))],
      ['条件建议汇总', 'condition_recommendation_summaries.json', '%d 组（%d 组有明确目标）' % (
          len(ADVICE), TARGETED)],
      ['LLM 主题', 'entity_advice_theme_summaries.json', '%d 组、%d 个实体、%d 条主题' % (
          GROUPS, ENTITIES, THEMES)]],
     [3.0, 8.0, 4.6]),
    ('p', '最终图为 %d 个节点、%d 条边。规模略小于上一轮（%d 节点），原因是线路层由 197 个候选收束为 29 条，步骤与相关边同步减少；差异来自线路层口径：此前片段亦计入线路。' % (
              len(GRAPH['nodes']), len(GRAPH['edges']), 6763)),
    ('p', '入库已完成：dataset 名 %s，%d 个节点、%d 条边，'
          '按 label 与 relation 分成批量 MERGE 写入 Neo4j 5.26（本地实例，bolt://localhost:7687），'
          '逐节点、逐条边用 payload_json 回读：'
          'reimport_no_duplicates、all_node_payloads_roundtrip、all_edge_payloads_roundtrip 均为 true，'
          '库内计数与产物一致；库内仅保留当前 dataset，旧数据清理数 0（首次导入）。'
          '过程见发布快照 validation.json，release.json 的 verified=true。' % (
              P6.get('dataset', ''), MANIFEST['nodes'], MANIFEST['edges'])),
    ('p', '入库之外还做了实际查询验证：以下语句在库上执行并核对返回，'
          '查询文本与返回保存在发布快照的 cleaned_queries.json 与 live_verification.json。'),
    ('tbl', '表6-5  实际查询返回与判断依据',
     ['查询', '库内实际返回', '判断依据'],
     [['官方路线步骤（JZG-OFF-01-R…）', '15 步；第 1 步 VISITS 返回 [原始森林, 沟口游客中心]',
       '步骤数与 routes.json 一致；collect 集合是该步涉及的地点，不是步内游览顺序'],
      ['POI → FACT', '115 个景点、378 条事实（其余 97 条关联至路线与交通，共 475）',
       '与 facts 产物计数一致'],
      ['CONDITIONAL_RULE', '1327 条，均带 condition 与 consequence 属性',
       '与 cleaned-v1/conditions.json 行数一致'],
      ['实体主题 advice_themes_json', '238 个实体带主题 JSON', '与 llm_theme_unique_entity_ids 一致；'
       'JSON 内按 scenic_code 过滤后使用，通用交通方式可能同时保存多个景区内容'],
      ['TRANSPORT_LEG', '105 条', '与 transport_legs.json 一致']],
     [4.4, 5.4, 4.8]),
    ('p', '主体网络视图将 ROUTE 经步骤到 POI 的访问关系投影为 ROUTE_VISITS_PROJECTED 派生边，'
          '便于观察线路覆盖与交织。它是可视化派生边，不是正式数据库新增关系，'
          '也不表示两个地点之间已确认可通达；完整的步骤、时间、条件、属性和证据仍在正式图中（图 6-1）。'),
    ('fig', 'P6/可视化可交互知识图谱/图6-1_九寨沟完整主体网络.png',
     '图6-1  九寨沟清理后的完整主体网络（49 个 POI、7 条路线、63 个交通路段）', 14.2),
]

CONTENT['6.5'] = [
    ('p', '全量网络仅适于总览，单个实体的信息密度须通过一跳邻域考察。以珠海"珊瑚自助餐厅"为例：'
          '两条条件规则（1.1 米以下儿童免费、早餐视客流情况逐步开放）、一条属性事实'
          '（所属横琴湾酒店）、一条时刻汇总、一条建议汇总和一条原始观察，'
          '全部直接关联到该景点，边上标注关系类型（图 6-2）。这类证据网络每个景区都有离线交互版：'
          '广州长隆 124 个主体节点、203 条边，珠海长隆 84 个节点、153 条边；'
          '页面支持搜索、类型筛选、缩放平移；点击节点在侧栏显示'
          '基本字段、线路步骤、交通端点、属性、时间汇总、'
          '条件建议与原始证据。'),
    ('fig', 'P6/可视化可交互知识图谱/图6-2_餐厅条件属性与证据.png',
     '图6-2  条件、属性与证据的一跳邻域（珠海"珊瑚自助餐厅"）', 14.2),
    ('p', '全量网络节点密集、连线交错（图 6-1，共 %d 个节点），静态图仅绘制一条线路（图 6-3）：' % len(GRAPH['nodes']) +
          '九寨沟官网的一日游线路，15 步、07:00 到 17:35、途经二十余个景点。蓝色圆点表示步骤，点上方标注该步新到访的景点，点下方标注原文记录的时刻。该图可直接呈现两点：路线为"单向推进"而非来回折返；下午三点以后连续串联六七处海子，该段为乘车经过，非步行。'),
    ('fig', 'P6/可视化可交互知识图谱/图6-3_官方线路结构.png',
     '图6-3  九寨沟官方一日线路的图谱结构（步骤 → 到访景点）', 15.0),
    ('p', '关系分布（图 6-4）中 SUPPORTED_BY 最多（%d 条）。每条时间汇总与建议汇总均须挂回原始观察，这是"可追溯"的代价——近半数边为溯源边。真正描述景区结构的边（HAS_STEP、VISITS、PRECEDES）合计 %d 条，仅占三成余。APPLIES_TO 有 %d 条，用于将条件规则挂接到对象，同样体现边分布的不均衡。' % (
              EDGES['SUPPORTED_BY'], EDGES['HAS_STEP'] + EDGES['VISITS'] + EDGES['PRECEDES'],
              EDGES['APPLIES_TO'])),
    ('fig', 'P6/可视化可交互知识图谱/图6-4_关系类型分布.png',
     '图6-4  15 种关系的条数分布', 14.0),
    ('p', '交互版页面仅用于展示与人工核查，不参与计算；正式产物是 Neo4j 里的图与 release.json。'),
]

CONTENT['6.6'] = [
    ('p', '质量验证分两层。结构层是硬检查：节点 ID 不重复、边不指向不存在的节点、'
          '边不重复、每条汇总都能挂回原始记录、被剔除的线路不得出现在正式图中。'
          '以上均为程序断言，不通过即终止，不存在"警告但继续"的路径。'),
    ('p', '内容层只能抽查。时间汇总共 %d 条，覆盖 %d 个实体，平均每个实体 %.1f 条——覆盖明显不足，因为 P5 抽取的事实共 %d 条，多数景点缺少时间信息。按景点统计，%d 个景点中有 %d 个没有任何时间汇总。第 7 章编排行程时常只能给出顺序、无法给出时刻，原因即在于此，与编排逻辑无关。' % (
              len(TIMES), ENTITIES_WITH_TIME, len(TIMES) / max(1, ENTITIES_WITH_TIME),
              len([n for n in GRAPH['nodes'] if n['label'] == 'FACT']),
              POI_TOTAL, POI_TOTAL - POI_WITH_TIME)),
    ('tbl', '表6-6  四步产物的覆盖情况',
     ['产物', '数量', '覆盖', '说明'],
     [['时间汇总', str(len(TIMES)), '覆盖 %d 实体；%d/%d 个景点' % (ENTITIES_WITH_TIME, POI_WITH_TIME, POI_TOTAL),
       '平均 %.1f 条/实体，覆盖不足' % (len(TIMES) / max(1, ENTITIES_WITH_TIME))],
      ['建议汇总', str(len(ADVICE)), '%d 组有明确目标（%d 组未定位）' % (TARGETED, len(ADVICE) - TARGETED),
       '%d 组因线路片段被排除；%d 条来源记录被隔离' % (
           QUARANTINED, len(MANIFEST['advice_audit']['quarantined_records']))],
      ['LLM 主题', str(THEMES), '%d 个实体、%d 组' % (ENTITIES, GROUPS), '每组一次调用，成功 %d 组' % GROUPS],
      ['事实', str(len([n for n in GRAPH['nodes'] if n['label'] == 'FACT'])), '—', '景点属性为主']],
     [2.6, 1.8, 3.6, 7.4]),
    ('p', '主题汇总存在一处容量取舍：珠海长隆海洋王国这一实体挂接 %d 个建议条目，单次调用无法容纳 30K 预算。处理方式是将条目二分、分两次调用、再按主题标签合并，而非扩大上下文窗口。代价是跨分片的同类主题无法合并——同一实体可能出现两条语义接近的主题，分别来自两个分片。该情况已记录在质量清单的 chunked_groups 字段中。' % CHUNK_ITEMS),
    ('p', '三处仍未闭合。图已导入本地 Neo4j 5.26 并逐节点、逐条边回读比对，库内计数与产物一致，release.json 的 verified=true——但数据库仅在本机运行，未对外提供服务。语义复核已按固定口径执行，但与主题生成共用同一方法体系，属同源自审，缺少独立第三方复核。同义主题未合并，质量清单中语义合并记为 false。每个主题开头统一添加"历史资料记载，"前缀，标明其为语料中的表述。'),
    ('p', '525 条主题已逐条复核：成立 354 条（67.4%）、牵强 159 条（30.3%）、不成立 0 条、'
          '无法核对 12 条（成员条目未在底表展示）。牵强样本集中于两类：摘要引入条目未包含的含义'
          '（补写时间地点、添加来源说法），以及将单条体验普遍化；分景区看广州最高（32.7%），'
          '九寨沟与珠海均约为 29%。明细见 outputs/P6/主题复核底表525条.csv。'),

]
