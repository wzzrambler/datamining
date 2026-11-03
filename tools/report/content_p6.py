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
THEMES = P6.get('llm_theme_count', 0)
GROUPS = P6.get('llm_theme_entity_scene_groups', 0)
ENTITIES = P6.get('llm_theme_unique_entity_ids', 0)
POI_WITH_TIME = len({t['target_id'] for t in TIMES})
POI_TOTAL = sum(1 for n in GRAPH['nodes'] if n['label'] == 'POI')
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
    ('p', '上游四章交出来的是四种东西：清洗后的正文（P1）、实体提及（P2）、候选路线（P3）、指代关联（P4）、'
          '条件与事实（P5）。把它们接起来靠的不是文本相似度，是 ID。'
          '实体的 entity_id 由类型、景区、来源类别和规范名一起哈希，和它出现在哪篇文档、第几个字无关，'
          '所以同一句"熊猫村"在 P2、P3、P5 里指向同一个节点，中间换了多少次写法都不影响。'
          '这条设计在重跑时救过一次：P1 重清洗之后所有字符偏移全变了，但实体 ID 一个没变，'
          '只有提及位置（mention_id）跟着变。'),
    ('p', '融合顺序是固定的四步，每步都有独立的验收：'
          '先做线路清理（把 P5 里 %d 个线路候选收成 %d 条正式线路），'
          '再汇总时间（同一实体同一属性上的多条时刻合成一个区间并集），'
          '再汇总条件建议（同一实体的多条建议按目标合并），最后挂 LLM 主题（把一堆条目压成几条人话主题）。'
          '顺序不能换——时间汇总要读清理后的线路，主题要读汇总后的建议。' % (
              CLEANING['before']['routes'], len([n for n in GRAPH['nodes'] if n['label'] == 'ROUTE']))),
    ('p', '外来数据一律不新增 ID。P5 里出现过没注册的地点（"沟口""诺日朗换乘"这类），'
          '处理办法是保留原步骤文字、不建节点，也不硬并到名字相近的景点上。'
          '这样做会让一部分步骤没有 VISITS 边，但比张冠李戴强——第 3 章那条"宁可少拼一条，不要拼错一条"'
          '在这里同样适用。'),
]

CONTENT['6.2'] = [
    ('p', '%d 个线路候选里只有 %d 条进了正式图。剩下的 %d 条不是"错"，是"不够格当线路"：'
          '%d 条只有单点（"南门进，看熊猫"），%d 条是段落里的一段局部序列，%d 条是纯交通片段'
          '（"坐大巴到沟口"），还有 %d 条是并列备选、顺序说不清。'
          '这些全部写进 route_fragments_audit.json，每条带拒绝理由和原文证据，没有静默丢弃。' % (
              CLEANING['before']['routes'], CLEANING['after']['routes'], CLEANING['fragments'],
              CLEANING['decisions']['single_stage'], CLEANING['decisions']['local_fragment'],
              CLEANING['decisions']['transport_fragment'],
              CLEANING['decisions']['ambiguous_alternatives'] + CLEANING['decisions']['unresolved_fragment'])),
    ('tbl', '表6-1  线路候选的判定分布',
     ['判定', '条数', '是否进正式图', '判定依据'],
     [[DECISION_CN[k], str(v), '是' if k in ('long_touring_sequence', 'timed_thematic_itinerary',
                                            'multi_park_overview') else '否',
       {'long_touring_sequence': '步骤 ≥5 且顺序明确、证据连续',
        'timed_thematic_itinerary': '带时刻分段，能看出一天怎么走',
        'multi_park_overview': '原文明确写了两园先后',
        'single_stage': '不足 2 个可排序站点',
        'local_fragment': '跨度只覆盖段落的一部分',
        'transport_fragment': '只有交通，没有游览点',
        'ambiguous_alternatives': '两条以上并列，顺序无证据',
        'unresolved_fragment': '证据互相矛盾或缺失'}[k]]
      for k, v in sorted(CLEANING['decisions'].items(), key=lambda kv: -kv[1])]
     + [['合计', str(CLEANING['before']['routes']), '%d 条' % CLEANING['after']['routes'], '—']],
     [3.6, 1.8, 2.6, 6.4]),
    ('p', '噪声里最麻烦的一类不是碎片，是"同名不同位置"。三篇游记都写了"熊猫馆"，'
          '但广州的熊猫馆和珠海的熊猫馆是两个地方，程序靠景区码分开了；'
          '同一景区里两个同名的"餐厅"就分不开，只能保留两条 mention、共用一个 entity_id，'
          '这是已知的欠账，体现在 P4 的"类型不符"和 P5 的"目标落不准"两处统计里。'),
    ('p', '还有一类是"父级挂错"：%d 条事实的 attribute 是 parent_park，本来想表达"这个景点属于哪个园区"，'
          '但抽出来有时会变成"这个景点在园区里"，方向反了。'
          '这类没有自动修，只在质量清单里记了一笔，用的时候要回原文看一眼。' % len(
              [f for f in (n['properties'] for n in GRAPH['nodes'] if n['label'] == 'FACT')
               if f.get('attribute') == 'parent_park'])),
]

CONTENT['6.3'] = [
    ('p', 'Schema 定了 10 个节点标签和 15 种关系。定的原则只有一条：一个节点只表示一种东西，'
          '关系只表示一个方向的断言。比如线路和步骤分成两层（ROUTE 与 ROUTE_STEP），'
          '因为一条线路的属性（性质、粒度、来源）和一步的属性（到访景点、时间、交通）根本不是一个量级；'
          '合在一层的话，查"这条线路经过几个景点"要先把所有步骤属性翻一遍。'),
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
    ('p', '属性存储有一处限制：Neo4j 的属性不能是嵌套对象或对象数组。'
          '办法是把完整负载压成 payload_json 存一份，同时把能展开的标量字段（名称、景区、类别）'
          '单独存成可查询的属性，列表里混杂类型时再拆成 xxx_json。'
          '这样既能用 Cypher 直接查，又能把原样数据取回来重算哈希——导入回读校验就是靠 payload_json。'),
]

CONTENT['6.4'] = [
    ('p', '构建分四步跑，全部有幂等要求：同样的输入跑两次，产物字节相同。'
          '线路清理和四步汇总都有各自的 manifest，记着输入哈希；上游一改，下游直接拒绝运行，'
          '不会出现"用新数据配旧参数"的半新半旧结果。上游内容变化时，下游会直接拒绝运行，'
          '需显式刷新指纹后才继续。'),
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
    ('p', '最终图 %d 个节点、%d 条边。规模比上一轮（%d 节点）小一点，因为线路层从 197 个候选收到 29 条，'
          '步骤与相关边同步减少，差异来自线路层口径：此前片段亦计入线路。' % (
              len(GRAPH['nodes']), len(GRAPH['edges']), 6763)),
    ('p', '入库包已经生成：dataset 名 %s，%d 个节点、%d 条边，'
          '按 label 和 relation 分成批量 MERGE 语句，导入后再用 payload_json 逐节点回读比对。'
          '这一步在本机没跑通——机器上没有 Neo4j 实例，只有包和离线自检。'
          'release.json 里如实写了 verified=false、neo4j_imported=false，'
          '接上库之后跑 tools/P6/publish_cleaned.py --apply 才算完成。' % (
              P6.get('dataset', ''), MANIFEST['nodes'], MANIFEST['edges'])),
]

CONTENT['6.5'] = [
    ('p', '图太大画不全，6649 个节点铺成一张图只会是一团线。所以静态图只画一条线路：'
          '九寨沟官网那条一日游，15 步、07:00 到 17:35、途经二十来个景点。'
          '蓝色圆点是步骤，点上面的字是这一步新到的景点，点下面的时间是原文写的时刻。'
          '这张图能一眼看出两件事：路线是"一条线走到底"而不是来回折返；'
          '下午三点以后一口气串了六七个海子，该段为乘车经过，非步行。'),
    ('fig', 'P6/可视化可交互知识图谱/图6-1_官方线路结构.png',
     '图6-1  九寨沟官方一日线路的图谱结构（步骤 → 到访景点）', 15.0),
    ('p', '关系分布（图6-2）说明这张图的重心在哪：SUPPORTED_BY 最多（%d 条），'
          '每条时间汇总和建议汇总都要挂回原始观察，这是"可追溯"的代价——'
          '几乎一半的边是溯源边。真正描述景区结构的边（HAS_STEP、VISITS、PRECEDES）加起来 %d 条，'
          '只占三成多。APPLIES_TO 有 %d 条，条件规则挂到对象上，这也是头重脚轻的一部分。' % (
              EDGES['SUPPORTED_BY'], EDGES['HAS_STEP'] + EDGES['VISITS'] + EDGES['PRECEDES'],
              EDGES['APPLIES_TO'])),
    ('fig', 'P6/可视化可交互知识图谱/图6-2_关系类型分布.png',
     '图6-2  15 种关系的条数分布', 14.0),
    ('p', '交互版另存了三个 HTML（outputs/P6/可视化可交互知识图谱/），按景区分，'
          '只放景点、线路、步骤三类节点，可以在浏览器里拖动、缩放、点开看属性。'
          '这份东西是给人看的，不参与任何计算，也不作为提交的正式产物。'),
]

CONTENT['6.6'] = [
    ('p', '质量验证分两层。结构层是硬检查：节点 ID 不重复、边不指向不存在的节点、'
          '边不重复、每条汇总都能挂回原始记录、被剔除的线路绝不出现在正式图里。'
          '这几条全是程序断言，不过就退出，没有"警告但继续"。'),
    ('p', '内容层只能抽查。时间汇总做了 %d 条，覆盖 %d 个实体，平均每个实体挂 1.6 条观察——'
          '这个数字很薄，因为 P5 抽到的事实一共才 %d 条，很多景点根本没有时间信息。'
          '%d 个景点里有 %d 个连一条时间汇总都没有。第 7 章排行程时经常只能给顺序、给不了时刻，'
          '根子在这里，不是 Agent 偷懒。' % (
              len(TIMES), POI_WITH_TIME,
              len([n for n in GRAPH['nodes'] if n['label'] == 'FACT']), POI_TOTAL, POI_TOTAL - POI_WITH_TIME)),
    ('tbl', '表6-5  四步产物的覆盖情况',
     ['产物', '数量', '覆盖', '说明'],
     [['时间汇总', str(len(TIMES)), '%d/%d 个景点' % (POI_WITH_TIME, POI_TOTAL), '平均 1.6 条观察支撑，很薄'],
      ['建议汇总', str(len(ADVICE)), '%d 组有明确目标' % TARGETED, '%d 组因来源是线路片段被隔离' % QUARANTINED],
      ['LLM 主题', str(THEMES), '%d 个实体、%d 组' % (ENTITIES, GROUPS), '一组一调用，成功 %d 组' % GROUPS],
      ['事实', str(len([n for n in GRAPH['nodes'] if n['label'] == 'FACT'])), '—', '景点属性为主']],
     [2.6, 1.8, 3.6, 7.4]),
    ('p', '主题汇总存在一处容量取舍：珠海长隆海洋王国这个实体挂了 %d 个建议条目，'
          '一次调用塞不进 30K 预算。做法不是放大窗口硬塞，而是把条目二分、跑两次、再按主题标签合并。'
          '代价是跨分片的同类主题合不起来——同一个实体可能出现两条意思接近的主题，'
          '分别来自两个分片。质量清单里记了 chunked_groups，报告里也不掩饰这一点。' % CHUNK_ITEMS),
    ('p', '以下三项尚未完成。'
          '一是没连库验证：导入包生成好了，但没有在真实 Neo4j 上跑过回读，'
          '所以 release.json 里 verified 是 false。'
          '二是主题的语义正确性没有独立复核，只有"结构和引用合法"这一层保证，'
          '每个主题还都强制以"历史资料记载，"开头，提醒读者这是语料里的说法、不是当前事实。'
          '三是同义主题没有合并，语义合并这一栏写的是 false。'),
]
