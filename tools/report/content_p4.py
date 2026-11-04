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
TRIG_CTX = {'这': '指正在讲的这个景点或这件事', '这里': '指当前段落所在的地点',
            '这个': '指刚提到的单个对象', '那': '回指更早的内容，常带对比',
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
REASON_NAME = {'type_mismatch': '候选类型与指代类型不符', 'type_distance_rule': '类型加分后仍不够',
               'event_candidate_missing': '事件候选没生成出来', 'no_candidate': '同句和前两句没有名词',
               'ambiguous': '两个候选分差不到 0.10', 'low_score': '最高分没过 0.65',
               'lexical_non_reference': '词表判定不是指代', 'event_structure_rule': '事件结构不完整',
               'discourse_expression': '话语标记，没有实体先行词'}
SCENE_NAME = {'GZCL': '广州长隆', 'ZHCL': '珠海长隆', 'JZG': '九寨沟'}
BY_SCENE = {}
for r in RECS:
    d = BY_SCENE.setdefault(r['scenic_code'], Counter())
    d[r['status']] += 1

CONTENT = {}

CONTENT['4.1'] = [
    ('p', '代词检测走两条路。一条是正则，把“这/这里/这个/那/他/它/它们/她/她们/这些/那些/这种”'
          '这类触发形式在全篇扫一遍；另一条不检测，直接用第 2 章已经识别出来的实体提及。'
          '两条路的结果并成一张候选表，最后得到 %d 条待消解记录，落在 %d 篇正文里'
          '（剩下 %d 篇从头到尾没有出现指代，这些文档不参与本章，但分词照样做了）。' % (
              len(RECS), DOCS_WITH, len(TOK['documents']) - DOCS_WITH)),
    ('p', '触发形式的分布很集中。“这”一个字出现了 %d 次，“这里”%d 次，两个加起来占了一半多。'
          '这和游记的写法有关：写完一段景点介绍，接着用“这里”“这个”把话头接下去。'
          '人称代词少得多，“他”%d 次，“它”%d 次，“她们”只有 %d 次。' % (
              TRIG['这'], TRIG['这里'], TRIG['他'], TRIG['它'], TRIG['她们'])),
    ('tbl', '表4-1  触发形式分布（共 %d 条）' % len(RECS),
     ['触发形式', '次数', '常见语境'],
     [[k or '（无触发）', str(v), TRIG_CTX.get(k, '没有独立触发区间，整条记录按事件处理')]
      for k, v in TRIG.most_common(10)],
     [2.6, 1.8, 10.2]),
    ('p', '区间长度上有个细节：%d 条指代只有一个字，%d 条两个字。'
          '单字的“这”麻烦最大——它后面可能跟量词（“这条线路”），也可能只是个指示词（“这时候”）。'
          '本版的处理是把“这个/这种/这些”这类两字形式整体当触发区间，单字“这”只取它自己，'
          '后面跟的完整名词另存一列 reference_phrase（“这条线路”“这种安排”），'
          '这样既不会把量词算进指代，也不会丢掉语义。这条规则是统一定的，没有照着标注答案调边界，'
          '代价是少数样本的区间和人工标注差一两个字，4.5 节里有一条就是这么错的。' % (ONE, TWO)),
    ('p', '候选池的规模差异很大：平均每条记录 %d 个候选，但有 %d 条记录一个候选都没有。'
          '这 %d 条不是程序出错，是那句话里同句和前两句确实没有名词性成分（“这样可以省很多时间”），'
          '这种直接判未消解，不进打分。反过来，有的记录候选堆到十几个，'
          '因为一段话里连着出现七八个景点名。' % (round(AVG_POOL, 1), EMPTY_POOL, EMPTY_POOL)),
]

CONTENT['4.2'] = [
    ('p', '人工标注只在 5 篇上做，共 %d 行，因为逐字核对整篇太慢，而这一章要的是“规则在真实指代上错在哪”，'
          '不是覆盖全量。5 篇里广州两篇、珠海一篇、九寨沟两篇，长的 4000 字、短的 800 字。' % EV['rows']),
    ('p', '这 %d 行里有 %d 行是两个来源：自动扫描命中的 %d 行，以及人工通读全文后补进来的 %d 行。'
          '人工补入的 16 行属自动扫描的漏检项——如“这里”位于长句中间，或“这儿”这类词表未收录的写法。'
          '两类都标 sample_origin，统计时可以分开看。' % (
              EV['rows'], 2, 35, 16)),
    ('tbl', '表4-2  标注构成',
     ['维度', '取值', '行数', '说明'],
     [['是否待消解', 'is_target=yes', '41', '需要给出先行词或判定无先行词'],
      ['是否待消解', 'is_target=no', '10', '扫描误命中，不是指代，不进分母'],
      ['标注状态', 'antecedent_found', '34', '有明确先行词，且能指出原文区间'],
      ['标注状态', 'non_entity_reference', '5', '“在这里感谢”这类话语标记'],
      ['标注状态', 'no_clear_antecedent', '2', '意思懂，但指不出具体是哪个'],
      ['来源', 'auto_detected / manual_added', '35 / 16', '自动命中 / 人工补入']],
     [3.0, 4.4, 1.8, 5.4]),
    ('p', '标注的字段不多，但每个偏移都要能回切原文：reference 是指代本身，sentence 是它所在的整句，'
          'context 是指代前后各取一段（前 65 字、后 85 字），antecedent 是先行词。'
          '四个区间都必须满足 content[start:end] 等于标注文本，差一个字即判定失效。'
          '正文一旦重新清洗，全部偏移需要按内容重新定位：先按整句在新正文中检索，'
          '再按句内偏移还原指代；先行词按片段检索，多处命中时取位于指代之前且最近的一处；'
          '上下文窗口按"前 65 字、后 85 字"重算。重定位只调整位置，不修改标注内容，'
          '原文片段被删除的直接报错。'),
    ('p', '难标的有两类。一类是事件先行词：原文列了两段路线清单，后面说“完全可以复制这条线路”，'
          '先行词是那两个区间，一个记录要挂两个先行区间（就是标出来的那 1 条多先行词样本）。'
          '另一类是话语标记：“在这里感谢写攻略的朋友”，这里的“这里”不是地点，'
          '没有实体可以指，只能标成 non_entity_reference，且不算错。'
          '这两类占了错误分析里的大部分篇幅，也是后面 4.5 节要分开统计的原因。'),
    ('p', '先行词按八类标注：人物、动物、地点、设施、交通、事件、话语标记、其他。'
          '判定顺序是先看被指对象的语义类别（人、动物、地点、设施、交通工具），'
          '再看是否指向一段事件区间（事件）；都不成立且只起衔接作用时记话语标记。'
          '找不到可指对象的记 no_clear_antecedent（本次 2 行），'
          '整行无法判断“是不是指代”的记 is_target=uncertain，该行不进入任何评价指标——本次 51 行中没有这种情况。'),
]

CONTENT['4.3'] = [
    ('p', '候选先行词来自三个通道。第一通道是第 2 章的实体提及，直接入池，'
          '因此“澳洲森林”“黑颈天鹅”这类复合专名不会被分词切成两块。'
          '第二通道是独立分词：jieba %s，HMM 关掉，把本篇出现过的景点全名注入自定义词典（%d 个词），'
          '全篇 %d 个词元，取名词性词性（n/ng/ns/nz/nr）作为补充候选，'
          '相邻的修饰词加中心词再拼一次，让只出现一次的复合专名也有机会。'
          '第三通道是事件：以动词、箭头链、编号结构为锚，生成可以回切原文的事件区间，最长 600 字，'
          '超了就记 generation_limits，不截断冒充完整事件。' % (
              TOK['documents'][0]['metadata']['version'],
              TOK['documents'][0]['metadata']['injected_words'], TOKENS)),
    ('p', '打分是三项加权：类型适配 0.45、距离 0.35、层级 0.20。距离按字符算，'
          '用 1/(1+d/12) 衰减，所以隔 12 个字掉一半分；层级同句给 1、上一句给 0.5、再往前给 0。'
          '类型适配看指代被判成什么：判成地点就只给地点和设施加分，判成动物就压掉地点分。'
          '最后过两道闸——最高分要到 0.65，且和次高分差至少 0.10，否则判歧义、不输出。'
          '两个阈值的作用是拦下“距离近但类型不符”的候选，取值来自开发集，未做独立标定。'),
    ('p', '候选不是想搜多远就搜多远。实体只搜同句和前两句，字符窗口 180；'
          '本地没有类型合适的候选时，允许回看紧邻的上一段，但同来源才算数；'
          '事件最多回看 1200 字。被这些约束挡掉的候选有 %d 次，'
          '挡得最多的是跨段落（%d）、主题或行程切换（%d）、正文与图片评论混接（%d）。'
          '该约束偏保守：宁可判定未消解，也不跨段落强行关联。' % (
              sum(BLOCKED.values()), BLOCKED['paragraph_boundary'],
              BLOCKED['topic_or_unverified_continuity'], BLOCKED['source_boundary'])),
    ('code', '{"reference": {"start": 137, "end": 139, "text": "这里", "type": "location"},\n'
             ' "candidates": [{"text": "鲸鲨馆", "semantic_type": "POI", "dist": 41, "score": 0.78},\n'
             '                {"text": "海底互动船", "semantic_type": "POI", "dist": 63, "score": 0.51}],\n'
             ' "status": "resolved", "antecedent": {"text": "鲸鲨馆", "start": 96, "end": 99},\n'
             ' "score": 0.78, "runner_up_score": 0.51, "rule_ids": ["P2_ENTITY", "LOCATION_MATCH"]}'),
    ('p', '每条记录都把候选、分数、次高分、命中的规则一起存下来。这么做是为了区分两种失败：'
          '候选里根本没有正确先行词（候选生成的问题），还是正确先行词在候选里但排第二（排序的问题）。'
          '两类问题的改法不同，混在一起只能得出“效果不好”。'),
]

CONTENT['4.4'] = [
    ('p', '%d 条记录里，给出关联的 %d 条，判未消解的 %d 条，排掉的 %d 条（词表判定不是指代），'
          '还有 %d 条是话语标记。关联率 %0.1f%%，偏低源于保守策略：'
          '第 6 章用这些关联连图谱，错连的代价高于漏连。' % (
              len(RECS), P4['status_counts']['resolved'], P4['status_counts']['unresolved'],
              P4['status_counts']['excluded'], P4['status_counts']['non_entity_reference'],
              P4['status_counts']['resolved'] / len(RECS) * 100)),
    ('tbl', '表4-3  未消解的原因分布',
     ['原因码', '条数', '含义'],
     [[k, str(v), REASON_NAME.get(k, k)]
      for k, v in sorted(P4['reason_counts'].items(), key=lambda kv: -kv[1])],
     [4.8, 1.8, 8.0]),
    ('p', '类型不符是最大头（%d 条）。典型情况是“这里”后面最近的候选是“排队”“拍照”这类动作名词，'
          '或者“这个地方”旁边写着“人很多”，程序拿不到地点类型的候选，就判未消解。'
          '排第二的是“候选类型对但分数不够”，多数是距离太远——正确的景点名在前面三四句，'
          '中间隔了一整段吐槽。' % P4['reason_counts'].get('type_mismatch', 0)),
    ('tbl', '表4-4  分景区的消解情况',
     ['景区', '记录数', '关联', '未消解', '排除', '关联率'],
     [[SCENE_NAME[k], str(sum(BY_SCENE[k].values())), str(BY_SCENE[k]['resolved']),
       str(BY_SCENE[k]['unresolved']), str(BY_SCENE[k]['excluded']),
       '%.1f%%' % (BY_SCENE[k]['resolved'] / sum(BY_SCENE[k].values()) * 100)]
      for k in ('GZCL', 'ZHCL', 'JZG')],
     [3.0, 2.2, 2.0, 2.4, 2.0, 2.4]),
    ('p', '九寨沟的关联率最低（%.1f%%），原因不在算法。九寨沟那几篇写的是“从诺日朗坐车到长海，'
          '再走栈道去五彩池”，指代多为“这里”“那边”，但前面铺的是交通和路线，'
          '实体密度低；而广州的游记一句一个馆名，“这里”很容易找到地点候选。'
          '这是语料性质，不是阈值设错了——把阈值降到 0.55 能把九寨沟的关联数提上去，'
          '但同时会多出一批错连，4.5 节的基线就是这种情况。' % (
              BY_SCENE['JZG']['resolved'] / sum(BY_SCENE['JZG'].values()) * 100)),
    ('p', '输出三份：coreferences.json 是正式产物，带候选、分数和规则号；'
          'tokenization.json 存 47 篇的分词结果和词典哈希，方便复盘“为什么这个词没被当候选”；'
          'outputs/P4 下的检查表和附注文本仅用于人工查阅，不进入下游处理。'),
]

CONTENT['4.5'] = [
    ('p', '评价只算 %d 行人工确认过的标注，其中 %d 行是待消解的目标。匹配规则是严格的：'
          '指代区间和先行词区间都要完全一致，多个先行词要集合完全相等，差一个字算错。'
          '不设“部分正确”，也不给边界重叠算分。' % (
              EV['rows'], EV['rules']['counts']['targets'])),
    ('tbl', '表4-5  规则系统与基线的对比（同一批标注、同一套区间匹配）',
     ['指标', '规则系统', '基线（取最近实体）', '说明'],
     [['输出关联数', str(EV['rules']['counts']['emitted_links']), str(EV['baseline']['counts']['emitted_links']),
       '基线敢连，规则敢不连'],
      ['关联正确数', str(EV['rules']['counts']['correct_links']), str(EV['baseline']['counts']['correct_links']),
       '严格区间一致才算对'],
      ['关联正确率', '%.1f%%' % (EV['rules']['metrics']['annotation_aligned_link_precision'] * 100),
       '%.1f%%' % (EV['baseline']['metrics']['annotation_aligned_link_precision'] * 100),
       '对已输出关联的精确度'],
      ['目标覆盖率', '%.1f%%' % (EV['rules']['metrics']['annotation_aligned_link_coverage'] * 100),
       '%.1f%%' % (EV['baseline']['metrics']['annotation_aligned_link_coverage'] * 100),
       '41 个目标里有多少拿到了关联'],
      ['检测召回', '%.1f%%' % (EV['rules']['metrics']['detection_recall_on_annotated_targets'] * 100),
       '%.1f%%' % (EV['baseline']['metrics']['detection_recall_on_annotated_targets'] * 100),
       '能认出“这里”是在指代，两套一样'],
      ['状态判断准确', '%.1f%%' % (EV['rules']['metrics']['status_accuracy'] * 100),
       '%.1f%%' % (EV['baseline']['metrics']['status_accuracy'] * 100),
       '消解/未消解/排除判对的比例']],
     [3.2, 2.4, 3.4, 5.6]),
    ('p', '规则系统输出 %d 条关联，对 %d 条，错 %d 条；基线输出 %d 条，对 %d 条。'
          '基线的覆盖率更高（%.1f%% 对 %.1f%%），状态判断也更准（%.1f%% 对 %.1f%%），'
          '因为它几乎总是指向最近的实体，判“未消解”的次数少。'
          '这就是取舍：基线比规则多连了 %d 条，代价是它自己的 %d 条里有 %d 条是错的；'
          '规则连得少，但已连部分正确率更高，两类指标不存在单向优劣。' % (
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
       {'event': '事件区间太长，边界定不住',
        'location': '本类最好，仍有 5 例被距离拖累',
        'facility': '设施名和地点名混在一起，类型判不出来',
        'animal': '指代笼统（“它们”指一群），单个动物名对不上',
        'other': '样本少，两类都没对'}[k]]
      for k, v in sorted(EV['rules']['single_antecedent_by_gold_type'].items(),
                         key=lambda kv: -kv[1]['targets'])],
     [2.2, 1.8, 1.6, 1.8, 7.2]),
    ('p', '三条错误各有具体成因。'
          'P4A-011 的“这样”，人工标的是“穿大口袋的衣服……就进去了”这一小句，'
          '程序把整句（连前面的“你可以背包进去”）都圈进去了，区间过宽；'
          'P4A-031 的“这个”人工指的是“容易忽略的海底互动船”，程序连到了同一段的“鲸鲨馆”，'
          '属于同名景点密集时的排序错误；P4A-038 的“这”两侧文本完全一样，'
          '都是“五彩池”，只是起点差了两个字，严格匹配下算错——该例属于边界规则的代价，而非理解错误。'),
    ('p', '各指标的分子分母写明：精确率 = 正确关联数 / 已输出关联数（规则 11/14，基线 8/23）；'
          '覆盖率 = 拿到关联的目标数 / 待消解目标数（14/41）；检测召回 = 检出目标数 / 待消解目标数（39/41）；'
          '状态判断准确 = 判对行数 / 全部标注行（规则 30/51，基线 34/51）。'
          '41 个待消解目标中含 5 行话语标记与 2 行无明确先行词——这 7 个在严格匹配下不可能拿到关联，'
          '只进覆盖率分母、不影响精确率；is_target=no 的 10 行不进入任何分母。'),
    ('p', '与 V1 版本相比，关联数由 5 条增至 %d 条，正确数由 4 条增至 %d 条，'
          '增量主要来自事件类（4 条正确）与复合专名类（“澳洲森林”“黑颈天鹅”等）。'
          '多先行词那 1 条依然失败：两个区间要一起命中才算对，程序只给出其中一个。' % (
              EV['rules']['counts']['emitted_links'], EV['rules']['counts']['correct_links'])),
    ('p', '这些数字不能当模型准确率用，原因有三。'
          '一是样本只有 5 篇、%d 个目标，错一条就是三个百分点；'
          '二是这 5 篇在规则开发时反复看过，属于“已见样本”，不是留出集；'
          '三是阈值 0.65、分差 0.10 和几条词表都是照着这批错误改的，'
          '换一批游记重测，正确率大概率会掉。'
          '可信的泛化指标需要另标 10 篇以上未参与调参的样本，本次实验未覆盖。' % EV['rules']['counts']['targets']),
]
