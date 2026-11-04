# -*- coding: utf-8 -*-
"""实验总结：失败现象、原因、改进、局限、突破点。"""
import json
from pathlib import Path

from facts import FACTS as F

ROOT = Path(__file__).resolve().parents[2]
P3, P4, P4E, P5, P6, P7 = F['p3'], F['p4'], F['p4_eval'], F['p5'], F['p6'], F['p7']
CLEAN = ROOT / 'processed/P6知识图谱/cleaned-v1'
GRAPH = json.loads((CLEAN / 'graph.json').read_text(encoding='utf-8')) if (CLEAN / 'graph.json').exists() else {'nodes': [], 'edges': []}
RULES = [n['properties'] for n in GRAPH['nodes'] if n['label'] == 'CONDITIONAL_RULE']
CLEANING = json.loads((CLEAN / 'cleaning_summary.json').read_text(encoding='utf-8')) if (CLEAN / 'cleaning_summary.json').exists() else {}
KIND = {}
for r in RULES:
    KIND[r['statement_kind']] = KIND.get(r['statement_kind'], 0) + 1
STATUS = P7.get('status') or {}
RUNS = P7.get('runs') or []
BAD_RUNS = sum(v for k, v in STATUS.items() if k not in ('ok', 'partial'))
REJ = (P3.get('candidate_status') or {})
REJ_ALL = sum(REJ.values()) or 1
INV = json.loads((ROOT / 'processed/P3路线层级挖掘/candidate_inventory.json').read_text(encoding='utf-8'))
REASONS = {}
for _d in INV:
    for _c in _d.get('candidates') or []:
        if _c.get('status') == 'rejected':
            REASONS[_c.get('reason')] = REASONS.get(_c.get('reason'), 0) + 1
REASON_CN = {'numbered_list_without_route_context': '编号列表但缺少路线语境',
             'insufficient_steps_or_distinct_poi': '可排序站点不足',
             'no_p2_location_support': '缺少实体识别支撑',
             'truncated_ocr_chain': 'OCR 截断的链条',
             'advisory_edge_not_whole_route': '只有首尾建议，不构成整条路线',
             'external_transport': '园区外交通描述',
             'outside_transport_or_no_visit_node': '园区外交通或无到访点',
             'non_route_topic_active': '该段并非路线主题',
             'no_complete_chain': '没有完整可排序链条',
             'no_destination_after_connective': '连接词后无目的地',
             'conditional_or_unrealized_action': '条件句或未发生动作'}
TOP_REASON = sorted(REASONS.items(), key=lambda kv: -kv[1])[:4]
UNRES = P4.get('status_counts', {}).get('unresolved', 0)
import sys as _sys
_sys.path.insert(0, str(ROOT / 'src/P7旅游规划Agent'))
import p7_agent as _P7A  # noqa: E402
ROUTE_N = len(_P7A.ROUTES)
ROUTE_TIME = sum(1 for r in _P7A.ROUTES if r['time_start'])
ROUTE_2D = sum(1 for r in _P7A.ROUTES if '二日' in r.get('sub_route', ''))
POI_TOTAL = sum(1 for n in GRAPH['nodes'] if n['label'] == 'POI')
TIMES = json.loads((CLEAN / 'entity_time_summaries.json').read_text(encoding='utf-8'))
# 时间汇总也有挂在非景点实体上的，得按 POI 本身数
POI_NO_TIME = sum(1 for n in GRAPH['nodes']
                  if n['label'] == 'POI' and n['id'] not in {t['target_id'] for t in TIMES})
ADV = json.loads((CLEAN / 'condition_recommendation_summaries.json').read_text(encoding='utf-8'))
ITEMS = [i for a in ADV for i in a['items']]
MERGED = sum(1 for i in ITEMS if len(i.get('source_record_ids') or []) > 1)
OTHER_CLAUSE = sum(1 for r in RULES for c in ((r.get('condition') or {}).get('clauses') or [])
                   if c.get('category') == 'other')
RECS = P4.get('records', 1) or 1

EVS = (F['p5'].get('evidence') or {})

CONTENT = {}

CONTENT['S1'] = [
    ('p', '失败分两类：一类在方法边界之外、应当拒答；另一类是应当做对却做错的。这一节只记录后者，'
          '并给出每一处的规模与可复核位置；前者在第 4 至 7 章的局限里说明。'),
    ('tbl', '表S-1  各阶段可复现的失败与规模',
     ['阶段', '失败现象', '规模', '可复核位置'],
     [['P1', '长截图 OCR 断行后与正文重复的残片仍有保留', '见 1.4 节的复读行统计',
       '辅助资料/raw_and_clean_parts.json'],
      ['P3', '候选区间未能构成路线（编号列表缺语境、站点不足、无实体支撑）',
       '拒绝 %d / 扫描事件 %d' % (REJ.get('rejected', 0), REJ_ALL),
       'candidate_inventory.json'],
      ['P4', '指代未消解（候选类型不符或分差不足）', '%d / %d 条记录' % (UNRES, RECS), 'coreferences.json'],
      ['P4', '单先行词消解错误', '%d / %d 个目标' % (
          P4E['rules']['counts']['single_antecedent_targets'] - P4E['rules']['counts']['correct_single_links'],
          P4E['rules']['counts']['single_antecedent_targets']), 'outputs/P4/评估/evaluation.json'],
      ['P5', '条件被段落边界切断，只能标为条件不完整', '%d / %d 条规则' % (
          KIND.get('incomplete_condition', 0), len(RULES)), 'conditions.json 的 statement_kind'],
      ['P6', '线路候选中的片段不能进正式图', '%d 个片段' % CLEANING.get('fragments', 0),
       'route_fragments_audit.json'],
      ['P7', '无法给出行程（无数据、反问、冲突）', '%d / %d 个场景' % (BAD_RUNS, len(RUNS)),
       'outputs/P7/agent_runs.json']],
     [1.6, 6.2, 3.4, 4.6]),
    ('p', 'P4 的未消解要拆开看：%d 条里 %d 条是候选池中没有类型匹配的实体，'
          '%d 条是找到了候选但分差不足、判为歧义。前者属于语料问题（那句话附近确实没有地点名词），'
          '后者属于阈值问题。两类混在一起统计，只能得出"准确率低"这种没有指向的结论。' % (
              UNRES, P4.get('reason_counts', {}).get('type_mismatch', 0),
              P4.get('reason_counts', {}).get('ambiguous', 0))),
    ('p', 'P3 的 %d 个扫描事件中，接受 %d 个、拒绝 %d 个、标记为段落边界 %d 个、重复抑制 %d 个。'
          '拒绝理由集中在四类：%s。边界事件指标题、日程切换、时段切换一类的位置标记，'
          '既不是候选也不是拒绝，统计时不应与候选混算。' % (
              REJ_ALL, REJ.get('accepted', 0), REJ.get('rejected', 0),
              REJ.get('boundary', 0), REJ.get('suppressed_duplicate', 0),
              '、'.join('%s %d 个' % (REASON_CN.get(k, k), v) for k, v in TOP_REASON))),
    ('p', 'P7 的失败全部落在"输入超出数据范围"一侧：库外景区、信息不足、跨景区一日各一例。'
          '三例均未强行生成行程，但也没有给出降级方案（例如"仅园区级"的替代建议），'
          '这属于功能缺口，不是判定错误。'),
]

CONTENT['S2'] = [
    ('p', '失败样本逐条归因后，原因收敛到四处，都不在模型能力这一层。'),
    ('p', '一是语料的书写方式。游记面向人阅读：单句常包含三四个景点、用箭头和表情作分隔符、'
          '时间写成"上午九十点钟"、把整篇攻略拍成图片让读者自己放大。'
          'P1 的清洗只能处理排版噪声，处理不了这种信息密度不均——同一段里可能既有五个可抽取景点，'
          '也有整段抒情。'),
    ('p', '二是行级匹配的粒度。P1 的跨来源去重按行判定，而截图文字会被 OCR 断成十几字一行，'
          '还夹着作者手写批注和识别错字（"⭐"认成"芬"）。行级精确匹配与 4-gram 覆盖率都要求'
          '"整行基本等于正文某一段"，断行之后两条判据都不成立，复读内容因此残留。'
          '这不是阈值松，是判定单位选错。'),
    ('p', '三是证据粒度。抽取记录若只挂到输入包，核对一条建议需逐句回溯原文；'
          'P2 的验收用例与 P4 的人工标注同样依赖裸字符偏移，上游改一处，下游整段失效。'
          '前者已由导出阶段的确定性定位解决（%d 条带引文的观察中 %d 条落到句级），'
          '标注与用例的重锚仍依赖脚本逐一处理。' % (
              EVS.get('records_with_quote', 0), EVS.get('span_records', 0))),
    ('p', '四是容量与完整的矛盾。单次调用装不下整条长路线（P5 有一个包耗尽上下文预算），'
          '单个实体的建议条目也可能超过预算（P6 有 1 个实体挂 106 条建议）。'
          '放宽窗口只是推迟问题；解法是切分，而切分必然损失跨片的语义合并。'),
]

CONTENT['S3'] = [
    ('p', '针对第二处，把判定单位从"整行"改为"行与正文某一行的对齐"：用序列对齐计算这一行有多少字'
          '能按序对上正文，容许中间夹杂批注与错字；同时让数字过半的行（时刻表）与不足 6 字的行'
          '（地图标签）直接放行，避免误删。改后复读行删除量从 705 行增至 769 行，多删 64 行、961 字，'
          '重复段落残留由 12 段降至 0 段（以编辑相似度 ≥0.8 计）。'),
    ('p', '针对第三处，两端分别处理。P5 侧把定位从模型移到程序：模型只抄原文，导出时在包内检索，'
          '精确匹配优先、规范化匹配回退，唯一命中写 span，多处命中或找不到退回 package。'
          '本次 %d 条引文创下 %d 条句级偏移，占带引文记录的 %.1f%%。'
          'P2 验收用例与 P4 人工标注侧改为按整句定位、再按句内偏移还原，先行词取"位于指代之前且最近"的一处，'
          '重锚只改位置、不改内容，片段找不到直接报错。' % (
              EVS.get('quotes', 0), EVS.get('span_records', 0),
              EVS.get('span_share_of_quote_records', 0) * 100)),
    ('p', '针对第四处，P5 将超预算的包按段落切成四片重跑，合并时校验原文覆盖一致、步骤不缺；'
          'P6 将超预算实体按条目二分，两片各自校验覆盖率，合并时按主题标签去重，'
          '并把分片信息写入质量清单。两处都以可审计的切分换取完整性。'),
    ('p', '针对 P7 的接口失配，三处一并修改：调用前按函数签名过滤参数，多余参数丢弃并记录诊断；'
          '景区名做别名归一，"九寨沟"落到"九寨沟风景名胜区"；同一调用连续失败两次即终止重试，'
          '由上层给出明确说明，避免把参数错误伪装成"没有数据"。'),
    ('p', '另一项是工程性加固：所有安全闸由 assert 改为显式抛错。Python 在 -O 模式下移除 assert，'
          '而 P6 的删库前检查、图谱结构校验都依赖这些断言，改后在任何运行模式下均有效。'),
]

CONTENT['S4'] = [
    ('p', '按当前实现，四类局限无法通过调参消除。'),
    ('p', '一是正确性缺少独立验证。P4 的 78.6% 精确率来自 5 篇已见样本，规则亦参照这批错误确定；'
          'P5 的人工核对仅覆盖 10 条；P6 的主题只验证结构与引用合法性，未复核语义；'
          'P7 的七个场景由设计者拟定。全链数字均属开发集表现，不是留出集指标。'),
    ('p', '二是覆盖率受语料密度限制。%d 条路线中仅 %d 条带时刻，两日路线 %d 条；'
          '%d 个景点中 %d 个没有任何时间汇总；条件规则中"其他"类别 %d 条，落不进受控类别。'
          '只能靠扩充语料或放宽证据要求解决，后者本方案不采用。' % (
              ROUTE_N, ROUTE_TIME, ROUTE_2D, POI_TOTAL, POI_NO_TIME, OTHER_CLAUSE)),
    ('p', '三是语义合并缺失。去重按字面执行，%d 条规则展开为 %d 个条目，由两条以上来源合并而成的仅 %d 个；'
          '主题不跨实体合并，同一建议在三个景区各存一份。图谱规模因此偏大，重复表达偏多。' % (
              len(RULES), len(ITEMS), MERGED)),
    ('p', '四是证据锚尚未贯通全链。P5 的引文已能落到句级，但带引文的记录中另有 %.1f%% 因改写或跨段拼接'
          '仍退回包级；P2 的验收用例与 P4 的标注仍是裸偏移，上游每改一次就要重锚一次；'
          '下游图谱节点也没有携带锚指纹，"哪条结论的依据变了"目前无法查询。' % (
              (1 - EVS.get('span_share_of_quote_records', 0)) * 100)),
]

CONTENT['S5'] = [
    ('p', '优先突破的技术点选"证据锚"：以内容寻址的方式记录每条结论的出处，替代裸字符偏移。'
          '本次实验已完成其中第一步——P5 的引文在导出阶段定位到句，'
          '其余三步（锚结构、逐条迁移、抽查入口）仍待实现。'),
    ('p', '选它有三个原因。一是可核验性：报告与图谱中的结论都声称有原文依据，而依据目前是'
          '"某文件第 1234 至 1250 个字符"，文件一换即无法复核，评审无法抽查，自身也无法回归。'
          '二是级联成本：P1 的清洗规则一旦调整，P2 至 P4 依赖偏移的标注与用例全部失效，'
          '需要逐个重锚，且仍需人工判断。三是通用性：该改造不改变任何阶段的语义，'
          '只改变证据的记录方式，对 P4 至 P7 的接口向后兼容。'),
    ('p', '预期收益是把"文件级哈希失效"降为"条目级失效"：上游变化时，只有真正引用变动文本的条目需要重跑，'
          '其余条目可按锚迁移，既降低重跑成本，也使"哪条结论的依据变了"成为可查询的事实。'),
]

CONTENT['S6'] = [
    ('p', '方案分四步。第二步已在本次实验中实现并验证，其余三步按同一结构续接。'),
    ('p', '第一步，定义锚结构。一条证据锚含四项：引文本身（10 至 80 字）、引文前后各 20 字的上下文指纹、'
          '所在段落的内容哈希、原偏移（快速路径）。校验顺序为：按偏移切片，命中即通过；'
          '否则以引文在段落内检索，唯一命中则重锚；多处命中判为歧义；找不到则标 missing，'
          '该条结论降级为待复核，不参与下游推理。'),
    ('p', '第二步，改造 P5 的抽取协议（已实现）。抽取协议要求模型回填原文引句，'
          '导出阶段由 evidence.py 在包内定位：精确匹配优先，标点与全半角不一致时用规范化串，'
          '唯一命中记 span，多处命中或找不到退回 package。本次 %d 条带引文记录中 %d 条落到句级，'
          '留下 %d 条包级记录，均为改写句或跨段拼接。定位由程序完成，模型只负责抄录，'
          '既避免算错偏移，也提高伪造引文的成本。' % (
              EVS.get('records_with_quote', 0), EVS.get('span_records', 0),
              EVS.get('records_with_quote', 0) - EVS.get('span_records', 0))),
    ('p', '第三步，把级联失效从整体拒绝改为逐条迁移。清单除文件哈希外再存条目锚指纹（引文哈希）；'
          '上游变化后先执行迁移，能定位的条目保留，定位失败的标 stale，只对 stale 部分重跑。'
          '以 P1 的清洗规则调整为例，受影响的是引用被删文本的少数条目，而非整条链。'),
    ('p', '第四步，提供独立抽查入口。给定任意结论，输出其文档、段落、句子与上下文，'
          '支持人工标注"正确/错误/无法判断"。目的是把核验成本从翻查数千字降到点击一次，'
          '使人工核验样本量从 10 条提高到 100 条以上，补上 S4 的第一与第四项局限。'),
    ('p', '验收标准：以现有三个重锚脚本为基线。当前流程在偏移失效后仍需人工判断，'
          '改造后应做到零人工介入，并对每条迁移给出判定依据（偏移命中/引文唯一命中/歧义/丢失）。'
          '若锚迁移率低于当前 %.1f%% 的水平，则判定方案不成立，退回改进检索策略。' % 97.2),
]
