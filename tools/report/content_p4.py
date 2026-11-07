# -*- coding: utf-8 -*-
"""第 4 章：共指消解。"""
import json
from collections import Counter
from pathlib import Path

from facts import FACTS as F

P4 = F['p4']
EV = F['p4_eval']
ROOT = Path(__file__).resolve().parents[2]
RECS = json.loads((ROOT / 'processed/P4共指消解/coreferences.json').read_text(encoding='utf-8'))['records']
TOK = json.loads((ROOT / 'processed/P4共指消解/tokenization.json').read_text(encoding='utf-8'))

TRIG = Counter((r.get('trigger') or {}).get('text') for r in RECS)
TRIG_CTX = {'这': '指当前所述景点或事件', '这里': '指当前段落所在的地点',
            '这个': '指前述单个对象', '那': '回指更早的内容，常带对比',
            '他': '指游客本人或同行的人', '它': '指动物、场馆，偶尔指一个项目',
            '它们': '指一群动物', '她': '指女性游客', '这些': '指刚列举的一组',
            '她们': '指同行的女性', '那个': '回指远处的单个对象', '那些': '回指更早列举的一组',
            '这种': '指刚描述的一类安排或现象'}
ONE = sum(1 for r in RECS if r['reference']['end'] - r['reference']['start'] == 1)
TWO = sum(1 for r in RECS if r['reference']['end'] - r['reference']['start'] == 2)
EMPTY_POOL = sum(1 for r in RECS if not (r.get('candidates') or []))
AVG_POOL = sum(len(r.get('candidates') or []) for r in RECS) / len(RECS)
BLOCKED = Counter()
for r in RECS:
    for k, v in (r.get('blocked_candidate_counts') or {}).items():
        BLOCKED[k] += v
DOCS_WITH = len({r['doc_id'] for r in RECS})
TOKENS = sum(len(p.get('tokens') or []) for d in TOK['documents'] for p in d['paragraphs'])
BLOCK_NAME = {'paragraph_boundary': '跨段落', 'topic_or_unverified_continuity': '主题或行程切换',
              'source_boundary': '正文/图片/评论混接', 'window_or_sentence_boundary': '超出句窗前两句',
              'local_candidate_available': '本段已有更合适候选'}
REASON_NAME = {'type_mismatch': '候选类型与指代类型不符', 'type_distance_rule': '类型加分后仍不足',
               'event_candidate_missing': '事件候选未生成', 'no_candidate': '同句与前两句无名词',
               'ambiguous': '两个候选分差不到 0.10', 'low_score': '最高分未达 0.65',
               'lexical_non_reference': '词表判定不是指代', 'event_structure_rule': '事件结构不完整',
               'discourse_expression': '话语标记，没有实体先行词'}
SCENE_NAME = {'GZCL': '广州长隆', 'ZHCL': '珠海长隆', 'JZG': '九寨沟'}
BY_SCENE = {}
for r in RECS:
    d = BY_SCENE.setdefault(r['scenic_code'], Counter())
    d[r['status']] += 1

CONTENT = {}

CONTENT['4.1'] = [
    ('p', '代词检测采用两条路径。第一条为正则匹配，在全篇扫描“这/这里/这个/那/他/它/它们/她/她们/这些/那些/这种”等触发形式；第二条不单独检测，直接复用第 2 章已识别的实体提及。两路结果合并为一张候选表，得到 %d 条待消解记录，分布于 %d 篇正文（其余 %d 篇全篇未出现指代，不参与统计，分词照常执行）。' % (
              len(RECS), DOCS_WITH, len(TOK['documents']) - DOCS_WITH)),
    ('p', '触发形式分布集中。“这”单字出现 %d 次，“这里”%d 次，两者合计超过半数。这与游记的书写方式有关：一段景点介绍之后，常以“这里”“这个”承接上文。人称代词明显较少，“他”%d 次，“它”%d 次，“她们”仅 %d 次。' % (
              TRIG['这'], TRIG['这里'], TRIG['他'], TRIG['它'], TRIG['她们'])),
    ('tbl', '表4-1  触发形式分布（共 %d 条）' % len(RECS),
     ['触发形式', '次数', '常见语境'],
     [[k or '（无触发）', str(v), TRIG_CTX.get(k, '没有独立触发区间，整条记录按事件处理')]
      for k, v in TRIG.most_common(10)],
     [2.6, 1.8, 10.2]),
    ('p', '区间长度上有一项细节：%d 条指代仅含一个字，%d 条含两个字。单字“这”的处理难度最高——其后可能接量词（“这条线路”），也可能仅作指示词（“这时候”）。本版将“这个/这种/这些”等两字形式整体作为触发区间，单字“这”仅取自身，后接的完整名词另存于 reference_phrase 列（“这条线路”“这种安排”），既避免把量词计入指代，也不丢失语义。规则统一划定，未依据标注答案调整边界；少数样本的区间与人工标注相差一两个字，4.5 节给出一例。' % (ONE, TWO)),
    ('p', '候选池规模差异较大：平均每条记录 %d 个候选，但有 %d 条记录候选为空。这 %d 条并非程序错误，而是该句及前两句确实不含名词性成分（“这样可以省很多时间”），此类记录直接判为未消解，不进入打分。另有部分记录候选多达十余个，原因是同一段中连续出现七八个景点名。' % (round(AVG_POOL, 1), EMPTY_POOL, EMPTY_POOL)),
]

CONTENT['4.2'] = [
    ('p', '人工标注仅在 5 篇上开展，共 %d 行：逐字核对整篇成本较高，且本章关注“规则在真实指代上的错误类型”，不追求全量覆盖。5 篇中广州两篇、珠海一篇、九寨沟两篇，最长 4000 字、最短 800 字。' % EV['rows']),
    ('p', '这 %d 行来自 %d 个渠道：自动扫描命中 %d 行，人工通读全文后补入 %d 行。人工补入的 16 行属自动扫描漏检项，例如“这里”位于长句中间，或“这儿”等词表未收录的写法。两类均标注 sample_origin，统计时可分别核算。' % (
              EV['rows'], 2, 35, 16)),
    ('tbl', '表4-2  标注构成',
     ['维度', '取值', '行数', '说明'],
     [['是否待消解', 'is_target=yes', '41', '需要给出先行词或判定无先行词'],
      ['是否待消解', 'is_target=no', '10', '扫描误命中，非指代，不计入分母'],
      ['标注状态', 'antecedent_found', '34', '有明确先行词，且能指出原文区间'],
      ['标注状态', 'non_entity_reference', '5', '“在这里感谢”这类话语标记'],
      ['标注状态', 'no_clear_antecedent', '2', '语义可理解，但无法确定具体所指'],
      ['来源', 'auto_detected / manual_added', '35 / 16', '自动命中 / 人工补入']],
     [3.0, 4.4, 1.8, 5.4]),
    ('p', '标注字段虽少，但每个偏移均须能回切原文：reference 是指代本身，sentence 是它所在的整句，'
          'context 是指代前后各取一段（前 65 字、后 85 字），antecedent 是先行词。'
          '四个区间都必须满足 content[start:end] 等于标注文本，相差一字即判定失效。'
          '正文重新清洗后，全部偏移须按内容重新定位：先按整句在新正文中检索，'
          '再按句内偏移还原指代；先行词按片段检索，多处命中时取位于指代之前且最近的一处；'
          '上下文窗口按"前 65 字、后 85 字"重算。重定位只调整位置，不修改标注内容，'
          '原文片段被删除时直接报错。'),
    ('p', '标注难点有两类。一类是事件先行词：原文列了两段路线清单，后面说“完全可以复制这条线路”，'
          '先行词是那两个区间，一条记录需关联两个先行区间（即标注中的 1 条多先行词样本）。'
          '另一类是话语标记：“在这里感谢写攻略的朋友”，其中“这里”不指地点，'
          '无对应实体，只能标为 non_entity_reference，且不计为错误。'
          '这两类占错误分析的主要部分，4.5 节因此分别统计。'),
    ('p', '先行词按八类标注：人物、动物、地点、设施、交通、事件、话语标记、其他。'
          '判定顺序为先考察被指对象的语义类别（人、动物、地点、设施、交通工具），'
          '再看是否指向一段事件区间（事件）；均不成立且仅起衔接作用时记为话语标记。'
          '无可指对象者记为 no_clear_antecedent（本次 2 行），'
          '整行无法判断是否为指代的记为 is_target=uncertain，该行不进入任何评价指标——本次 51 行中不存在该情形。'),
]

CONTENT['4.3'] = [
    ('p', '候选先行词来自三个通道。第一通道为第 2 章的实体提及，直接入池，使“澳洲森林”“黑颈天鹅”一类复合专名不被分词切分。第二通道为独立分词：jieba %s，关闭 HMM，将本篇出现的景点全名注入自定义词典（%d 个词），全篇 %d 个词元，取名词性词性（n/ng/ns/nz/nr）作为补充候选，并将相邻修饰词与中心词再拼接一次。第三通道为事件：以动词、箭头链、编号结构为锚，生成可回切原文的事件区间，最长 600 字；超出时记入 generation_limits，不截断后冒充完整事件。' % (
              TOK['documents'][0]['metadata']['version'],
              TOK['documents'][0]['metadata']['injected_words'], TOKENS)),
    ('p', '打分由三项加权构成：类型适配 0.45、距离 0.35、层级 0.20。距离按字符计算，采用 1/(1+d/12) 衰减，相隔 12 字时得分减半；层级同句取 1、上一句取 0.5、更早取 0。类型适配依据指代的判定类别：判为地点仅对地点与设施加分，判为动物则压低地点分。另设两道阈值——最高分须达到 0.65，且与次高分的差值不低于 0.10，否则判为歧义、不予输出。阈值用于拦截“距离近但类型不符”的候选，取自开发集，未独立标定。'),
    ('p', '候选检索范围设有上限。实体仅检索同句与前两句，字符窗口 180；本地缺少类型匹配的候选时，允许回看紧邻上一段，但须来源相同；事件最多回看 1200 字。被上述约束拦截的候选共 %d 次，其中跨段落（%d）最多，主题或行程切换（%d）、正文与图片评论混接（%d）次之。该约束偏保守：宁可判定未消解，也不跨段落强行关联。' % (
              sum(BLOCKED.values()), BLOCKED['paragraph_boundary'],
              BLOCKED['topic_or_unverified_continuity'], BLOCKED['source_boundary'])),
    ('code', '{"reference": {"start": 137, "end": 139, "text": "这里", "type": "location"},\n'
             ' "candidates": [{"text": "鲸鲨馆", "semantic_type": "POI", "dist": 41, "score": 0.78},\n'
             '                {"text": "海底互动船", "semantic_type": "POI", "dist": 63, "score": 0.51}],\n'
             ' "status": "resolved", "antecedent": {"text": "鲸鲨馆", "start": 96, "end": 99},\n'
             ' "score": 0.78, "runner_up_score": 0.51, "rule_ids": ["P2_ENTITY", "LOCATION_MATCH"]}'),
    ('p', '每条记录均保存候选、分数、次高分与命中规则。该设计用于区分两类失败：候选集中不含正确先行词（候选生成问题），或正确先行词存在但排序第二（排序问题）。两类问题的改进方式不同，合并统计只能得出“效果不好”这一无指向的结论。'),
]

CONTENT['4.4'] = [
    ('p', '%d 条记录中，给出关联 %d 条，判为未消解 %d 条，排除 %d 条（词表判定并非指代），另有 %d 条为话语标记。关联率 %0.1f%% 偏低，源于保守策略：第 6 章以这些关联连接图谱，错误关联的代价高于漏连。' % (
              len(RECS), P4['status_counts']['resolved'], P4['status_counts']['unresolved'],
              P4['status_counts']['excluded'], P4['status_counts']['non_entity_reference'],
              P4['status_counts']['resolved'] / len(RECS) * 100)),
    ('tbl', '表4-3  未消解的原因分布',
     ['原因码', '条数', '含义'],
     [[k, str(v), REASON_NAME.get(k, k)]
      for k, v in sorted(P4['reason_counts'].items(), key=lambda kv: -kv[1])],
     [4.8, 1.8, 8.0]),
    ('p', '类型不符占比最高（%d 条）。典型情形是“这里”之后最近的候选为“排队”“拍照”一类动作名词，或“这个地方”邻近文本为“人很多”，程序无法取得地点类型候选，因而判为未消解。其次为“候选类型匹配但分数不足”，多数源于距离过远——正确的景点名位于前三四句，中间隔着整段叙述。' % P4['reason_counts'].get('type_mismatch', 0)),
    ('tbl', '表4-4  分景区的消解情况',
     ['景区', '记录数', '关联', '未消解', '排除', '关联率'],
     [[SCENE_NAME[k], str(sum(BY_SCENE[k].values())), str(BY_SCENE[k]['resolved']),
       str(BY_SCENE[k]['unresolved']), str(BY_SCENE[k]['excluded']),
       '%.1f%%' % (BY_SCENE[k]['resolved'] / sum(BY_SCENE[k].values()) * 100)]
      for k in ('GZCL', 'ZHCL', 'JZG')],
     [3.0, 2.2, 2.0, 2.4, 2.0, 2.4]),
    ('p', '九寨沟的关联率最低（%.1f%%），原因不在算法。九寨沟游记多为“从诺日朗坐车到长海，再走栈道去五彩池”一类表述，指代以“这里”“那边”为主，前文以交通与路线叙述为主，实体密度较低；广州游记则逐句列出场馆名称，“这里”易于找到地点候选。这属于语料性质，并非阈值设置不当——阈值降至 0.55 可提高九寨沟的关联数，同时引入一批错误关联，4.5 节的基线即对应此情形。' % (
              BY_SCENE['JZG']['resolved'] / sum(BY_SCENE['JZG'].values()) * 100)),
    ('p', '输出三份文件：coreferences.json 为正式产物，附候选、分数与规则号；tokenization.json 保存 47 篇的分词结果与词典哈希，便于复盘“某个词未被列为候选的原因”；outputs/P4 下的检查表与附注文本仅供人工查阅，不进入下游处理。'),
]

CONTENT['4.5'] = [
    ('p', '评价仅计入 %d 行人工确认的标注，其中 %d 行为待消解目标。匹配规则严格：指代区间与先行词区间须完全一致，多先行词须集合完全相等，相差一字即判错。不设“部分正确”，亦不对边界重叠计分。' % (
              EV['rows'], EV['rules']['counts']['targets'])),
    ('tbl', '表4-5  规则系统与基线的对比（同一批标注、同一套区间匹配）',
     ['指标', '规则系统', '基线（取最近实体）', '说明'],
     [['输出关联数', str(EV['rules']['counts']['emitted_links']), str(EV['baseline']['counts']['emitted_links']),
       '基线倾向关联，规则倾向弃答'],
      ['关联正确数', str(EV['rules']['counts']['correct_links']), str(EV['baseline']['counts']['correct_links']),
       '须严格区间一致'],
      ['关联正确率', '%.1f%%' % (EV['rules']['metrics']['annotation_aligned_link_precision'] * 100),
       '%.1f%%' % (EV['baseline']['metrics']['annotation_aligned_link_precision'] * 100),
       '对已输出关联的精确度'],
      ['目标覆盖率', '%.1f%%' % (EV['rules']['metrics']['annotation_aligned_link_coverage'] * 100),
       '%.1f%%' % (EV['baseline']['metrics']['annotation_aligned_link_coverage'] * 100),
       '41 个目标中获得关联的比例'],
      ['检测召回', '%.1f%%' % (EV['rules']['metrics']['detection_recall_on_annotated_targets'] * 100),
       '%.1f%%' % (EV['baseline']['metrics']['detection_recall_on_annotated_targets'] * 100),
       '能够识别“这里”属于指代，两套一致'],
      ['状态判断准确', '%.1f%%' % (EV['rules']['metrics']['status_accuracy'] * 100),
       '%.1f%%' % (EV['baseline']['metrics']['status_accuracy'] * 100),
       '消解/未消解/排除判对的比例']],
     [3.2, 2.4, 3.4, 5.6]),
    ('p', '规则系统输出 %d 条关联，其中正确 %d 条、错误 %d 条；基线输出 %d 条，正确 %d 条。基线覆盖率更高（%.1f%% 对 %.1f%%），状态判断也更准（%.1f%% 对 %.1f%%），因其几乎总是指向最近实体，判“未消解”的次数较少。取舍在于：基线多关联 %d 条，但 %d 条中有 %d 条错误；规则关联较少，已关联部分的正确率更高。' % (
              EV['rules']['counts']['emitted_links'], EV['rules']['counts']['correct_links'],
              EV['rules']['counts']['emitted_links'] - EV['rules']['counts']['correct_links'],
              EV['baseline']['counts']['emitted_links'], EV['baseline']['counts']['correct_links'],
              EV['baseline']['metrics']['annotation_aligned_link_coverage'] * 100,
              EV['rules']['metrics']['annotation_aligned_link_coverage'] * 100,
              EV['baseline']['metrics']['status_accuracy'] * 100,
              EV['rules']['metrics']['status_accuracy'] * 100,
              EV['baseline']['counts']['emitted_links'] - EV['rules']['counts']['emitted_links'],
              EV['baseline']['counts']['emitted_links'],
              EV['baseline']['counts']['emitted_links'] - EV['baseline']['counts']['correct_links'])),
    ('tbl', '表4-6  按先行词类型的成功率（单先行词 %d 个）' % EV['rules']['counts']['single_antecedent_targets'],
     ['类型', '目标数', '正确', '成功率', '暴露的问题'],
     [[{'event': '事件', 'location': '地点', 'facility': '设施', 'animal': '动物', 'other': '其他'}[k],
       str(v['targets']), str(v['correct_links']), '%.0f%%' % (v['success_rate'] * 100),
       {'event': '事件区间过长，边界难以确定',
        'location': '本类成功率最高，仍有 5 例受距离影响',
        'facility': '设施名与地点名混合，类型难以判别',
        'animal': '指代笼统（“它们”指一群），与单个动物名不匹配',
        'other': '样本较少，两类均未命中'}[k]]
      for k, v in sorted(EV['rules']['single_antecedent_by_gold_type'].items(),
                         key=lambda kv: -kv[1]['targets'])],
     [2.2, 1.8, 1.6, 1.8, 7.2]),
    ('p', '三条错误各有具体成因。'
          'P4A-011 的“这样”，人工标注为“穿大口袋的衣服……就进去了”这一短句，'
          '程序将整句（含前置内容“你可以背包进去”）一并纳入，区间过宽；'
          'P4A-031 的“这个”人工指的是“容易忽略的海底互动船”，程序关联到同一段的“鲸鲨馆”，'
          '属于同名景点密集时的排序错误；P4A-038 的“这”两侧文本完全一样，'
          '都是“五彩池”，仅起点相差两个字，在严格匹配下判为错误——该例反映边界规则的代价，而非理解错误。'),
    ('p', '各指标的分子与分母如下：精确率 = 正确关联数 / 已输出关联数（规则 11/14，基线 8/23）；覆盖率 = 获得关联的目标数 / 待消解目标数（14/41）；检测召回 = 检出目标数 / 待消解目标数（39/41）；状态判断准确 = 判对行数 / 全部标注行（规则 30/51，基线 34/51）。41 个待消解目标中含 5 行话语标记与 2 行无明确先行词，这 7 行在严格匹配下无法获得关联，仅计入覆盖率分母，不影响精确率；is_target=no 的 10 行不计入任何分母。'),
    ('p', '与 V1 版本相比，关联数由 5 条增至 %d 条，正确数由 4 条增至 %d 条，增量主要来自事件类（4 条正确）与复合专名类（“澳洲森林”“黑颈天鹅”等）。多先行词那 1 条仍未通过：两个区间须同时命中方判为正确，程序仅给出其中一个。' % (
              EV['rules']['counts']['emitted_links'], EV['rules']['counts']['correct_links'])),
    ('p', '这些数字不能作为模型准确率。样本仅 5 篇、%d 个目标，错一条即影响三个百分点；这 5 篇在规则开发阶段反复查阅，属“已见样本”而非留出集；阈值 0.65、分差 0.10 与部分词表均参照这批错误调整，更换游记样本重测后正确率大概率下降。可信的泛化指标需另行标注 10 篇以上未参与调参的样本，本次实验未覆盖。' % EV['rules']['counts']['targets']),
]
