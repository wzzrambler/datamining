# -*- coding: utf-8 -*-
"""第 5 章：条件建议抽取。"""
import json
import re
from collections import Counter
from pathlib import Path

from facts import FACTS as F

P5 = F['p5']
P6 = F['p6']
ROOT = Path(__file__).resolve().parents[2]
CLEAN = ROOT / 'processed/P6知识图谱/cleaned-v1'
GRAPH = json.loads((CLEAN / 'graph.json').read_text(encoding='utf-8'))
ADVICE = json.loads((CLEAN / 'condition_recommendation_summaries.json').read_text(encoding='utf-8'))
_MAN = ROOT / 'processed/P5条件建议抽取/runs' / (P5.get('run_dir') or '') / 'input_manifest.json'
MANIFEST = json.loads(_MAN.read_text(encoding='utf-8')) if _MAN.exists() else {}
PK = Counter(x['package_kind'] for x in MANIFEST.get('effective_packages') or [])
PK_ORIG = MANIFEST.get('original_packages') or P5.get('packages_prepared', 0)
PK_RUN = MANIFEST.get('attempted_packages') or P5.get('packages_attempted', 0)
RULES = [n['properties'] for n in GRAPH['nodes'] if n['label'] == 'CONDITIONAL_RULE']
FACTS = [n['properties'] for n in GRAPH['nodes'] if n['label'] == 'FACT']
ITEMS = [i for a in ADVICE for i in a['items']]
KIND_NAME = {'general_advice': '一般建议', 'conditional_advice': '条件建议', 'conditional_fact': '条件事实',
             'incomplete_condition': '条件不完整', 'general_restriction': '一般限制',
             'conditional_restriction': '条件限制'}
FORCE_NAME = {'recommended': '推荐', 'required': '必需', 'prohibited': '禁止', 'avoid': '不建议',
              'optional': '可选', 'descriptive': '只是描述', 'unspecified': '未表态'}
CAT_NAME = {'date_time': '日期时段', 'other': '其他', 'audience': '人群', 'ticket': '门票',
            'access': '进出园', 'crowding': '拥挤', 'time_budget': '时间预算', 'weather': '天气',
            'service_availability': '服务可用', 'fitness': '体力'}
BY_KIND = Counter(r['statement_kind'] for r in RULES)
BY_FORCE = Counter(r['force'] for r in RULES)
CLAUSES = Counter(len((r.get('condition') or {}).get('clauses') or []) for r in RULES)
LOGIC = Counter((r.get('condition') or {}).get('logic') for r in RULES)
COMPLETE = sum(1 for r in RULES if r.get('p5_complete'))
USAGE = P5.get('usage') or {}
REQ = P5.get('requests', 0) or 0
PROMPT_T = USAGE.get('prompt_tokens', 0) or 0
CACHE_T = USAGE.get('prompt_cache_hit_tokens', 0) or 0
MISS_T = USAGE.get('prompt_cache_miss_tokens', 0) or 0
OUT_T = USAGE.get('completion_tokens', 0) or 0
CACHE_PCT = CACHE_T / PROMPT_T * 100 if PROMPT_T else 0.0
ATTR = Counter(f['attribute'] for f in FACTS)

AUD_RULES = [('带小孩', '宝宝|娃|小孩|儿童|孩子|身高|亲子|小朋友|小盆友'),
             ('住店客人', '住客|酒店|入住|住宿'),
             ('带长辈', '老人|父母|长辈|爸妈')]


def _aud_bucket(text):
    """人群条件的关键词粗分，只用于描述性统计。"""
    for name, pattern in AUD_RULES:
        if re.search(pattern, text or ''):
            return name
    return '其他人群'


SCENE_CN = {'GZCL': '广州长隆', 'ZHCL': '珠海长隆', 'JZG': '九寨沟'}
SAMPLE = {'date_time': '“4 月 1 号已开放”“旺季”', 'other': '“人多的时候”“第一次来”',
          'audience': '“带娃”“身高 1 米到 1.5 米”', 'ticket': '“需提前一天 23:59 前预订”',
          'access': '“如需自驾须持自驾车票”', 'crowding': '“假期人很多”',
          'time_budget': '“时间充足的”', 'weather': '“下雨天”“晴天”',
          'service_availability': '“园区内租借的推车”', 'fitness': '“走不动太久”“恐高”'}
BY_KIND_SCENE = {sc: Counter(r['statement_kind'] for r in RULES if r['scenic_code'] == sc)
                 for sc in ('GZCL', 'ZHCL', 'JZG')}
CAT_TOTAL = Counter()
for _r in RULES:
    for _cl in ((_r.get('condition') or {}).get('clauses') or []):
        CAT_TOTAL[_cl.get('category')] += 1
AUD_BUCKET = Counter()
for _r in RULES:
    for _cl in ((_r.get('condition') or {}).get('clauses') or []):
        if _cl.get('category') == 'audience':
            AUD_BUCKET[_aud_bucket(_cl.get('text'))] += 1

EV = P5.get('evidence') or {}

CONTENT = {}

CONTENT['5.1'] = [
    ('p', '这一章要抽的不是"景点在哪"，而是"什么条件下该做什么"：下雨带雨衣、儿童 1 米到 1.5 米买优惠票、'
          '自驾要另买自驾车票。这类信息在游记里散着，句式也散，规则写不全，所以这一章交给模型抽取，'
          '但只让它做一件事——把一段原文翻成若干条结构化记录，别的一概不管。'),
    ('p', '输入切法沿用包（package）的概念：原计划 %d 个包（段落包 %d 个、路线包 %d 个）；'
          '其中 1 个路线包超预算，按段落切成 %d 片重跑，因此实际执行 %d 个包。'
          '每个包是一到几个完整段落加上该段的来源信息。' % (
              PK_ORIG, PK.get('paragraph', 0), PK_ORIG - PK.get('paragraph', 0),
              PK.get('route_fragment', 0), PK_RUN) +
          '包不能太大——模型要在一次调用里读完原文再一次性交出结果，超了就只能丢内容；'
          '也不能太小，否则"如果下雨就不建议去"这种跨句条件会被切断。'),
    ('tbl', '表5-1  抽取工具与产出',
     ['工具', '产出', '关键字段', '本次条数'],
     [['submit_poi_facts', '景点事实', 'attribute / value_text / modality / condition', str(len(FACTS))],
      ['submit_conditional_advice', '条件与建议', 'condition / consequence_text / force / targets', str(len(RULES))],
      ['submit_route', '路线候选', 'steps / order / evidence', '%d 条新路线' % P5.get('new_routes_extracted', 0)],
      ['submit_transport_leg', '交通路段', 'from / to / transport / duration', str(P5.get('transport_legs', 0))]],
     [4.2, 3.0, 5.4, 2.0]),
    ('p', '规则层只做两件事：定字段，定边界。字段为硬性约束——条件必须有 text 和 clauses，'
          '条款要落到受控类别上（日期时段、门票、人群、天气、拥挤、体力、进出园、服务可用、时间预算、其他）；'
          '建议必须有 consequence_text 和 force（推荐、必需、禁止、不建议、可选、只是描述、未表态）；'
          '每条记录都必须挂回一个输入包。切分边界由模型判断——一句话拆成几条记录由其自行决定，但每条都必须能追溯到对应原文段落。'),
    ('p', '引句定位不由模型完成：模型只负责把原文抄进 consequence_text / value_text，'
          '偏移由导出程序在包内检索。先按原字符串精确匹配，标点或全半角不一致时退到规范化串，'
          '唯一命中记为 span 并写下起止位置，多处命中或找不到则退回 package，不做模糊猜测。'
          '本次 %d 条带引文的观察中 %d 条定位到句（%.1f%%），共 %d 条引文；'
          '未定位的 %d 条多为改写过的短句与跨段拼接，保留包级来源。' % (
              EV.get('records_with_quote', 0), EV.get('span_records', 0),
              EV.get('span_share_of_quote_records', 0) * 100, EV.get('quotes', 0),
              EV.get('records_with_quote', 0) - EV.get('span_records', 0))),
    ('p', '让模型报字符偏移是另一种做法，但模型算偏移经常出错，错了以后整条记录要么报废、'
          '要么被迫相信一个错位置。把定位交给确定性程序，模型只承担抄录，'
          '既避免偏移算错，也让伪造引文的成本变高：抄错的话在原文里找不到，只能退回包级。'),
]

CONTENT['5.2'] = [
    ('p', '共提交 %d 个包，全部返回 COMPLETED。合计得到 %d 条观察，' % (
        P5.get('packages_attempted', 0), P5.get('active_completed_observations', 0)) +
          '从中整理出景点事实 %d 条、条件与建议 %d 条、交通路段 %d 条。'
          '观察数比记录数多，因为一次提交可以带多条记录——'
          '一段话里写了三个景点的开放时间，就是一条观察、三条事实。' % (
              len(FACTS), len(RULES), P5.get('transport_legs', 0))),
    ('tbl', '表5-2  批量运行开销',
     ['项目', '数值', '说明'],
     [['输入包', str(P5.get('packages_prepared', 0)), '路线包、段落包与补跑分片之和'],
      ['请求数', '{:,}'.format(REQ), '平均每包 %.1f 次（含重试）' % (REQ / max(1, P5.get('packages_attempted', 1)))],
      ['输入 tokens', '{:,}'.format(PROMPT_T), '其中缓存命中 {:,}（{:.1f}%）'.format(CACHE_T, CACHE_PCT)],
      ['未命中计费 tokens', '{:,}'.format(MISS_T), '按原价计价的输入部分'],
      ['输出 tokens', '{:,}'.format(OUT_T), '结构化 JSON'],
      ['单次最大提示 tokens', '{:,}'.format(P5.get('max_actual_prompt_tokens', 0)), '最长的一个包（整条路线）'],
      ['模型', 'deepseek-flash', '温度 0，强制 JSON 输出']],
     [3.6, 3.4, 7.6]),
    ('p', '开销集中在输入侧，但计价不能只看输入总数：%d 次请求共 %s 输入 token，'
          '其中 %s 命中前缀缓存（%.1f%%），按未命中计价的只有 %s；输出 %s token，平均每次 %.0f。'
          '缓存命中来自固定的系统提示与工具 schema——每个包只在末尾追加自己的正文，'
          '同一批次里除首次请求外，前缀部分几乎全部复用。' % (
              REQ, '{:,}'.format(PROMPT_T), '{:,}'.format(CACHE_T), CACHE_PCT,
              '{:,}'.format(MISS_T), '{:,}'.format(OUT_T), OUT_T / max(1, REQ))),
    ('p', '批处理按包复用：检查点记下包指纹（包内容、模型配置、实现哈希、提示词资产），'
          '重跑时指纹一致的包直接拷结果、不再请求，不一致的照常发起。'
          '上游只改少量文档时，开销按变更面而不是全量计。'),
    ('p', '一个包含有整条九寨沟官方两日路线的包触发了上下文预算上限。该包按段落切为四片补跑，'
          '校验原文覆盖与步骤集合不变后合并回批次，因此实际执行 %d 个包（原计划 %d，'
          '失败 1 个、补跑 %d 片）。' % (PK_RUN, PK_ORIG, PK.get('route_fragment', 0))),
    ('p', '复杂表达主要三类。第一类是多条件："晴天且非节假日才建议走西线"这种，'
          'clauses 会挂两条、logic 标 all；本次 %d 条条件里有 %d 条是两个以上子句，%d 条用了 all 或 any。'
          '第二类是条件藏在动词里："走不动就别走栈道"，没有"如果"两个字，'
          '模型要把"走不动"识别成体力条件，这类最容易漏。'
          '第三类是口语写法："带娃""小盆友""特种兵"，人群字段是自由文本，不做归一，'
          '留给后面的统计算粗分类。' % (
              len(RULES), sum(v for k, v in CLAUSES.items() if k >= 2),
              LOGIC.get('all', 0) + LOGIC.get('any', 0))),
    ('p', '还有 %d 条判成"条件不完整"——模型读到了一半条件，后半句被段落切断，或者原文本身就写了一半。'
          '这些没有丢掉，也没有硬补，单独标成 incomplete_condition 留在库里，'
          '第 7 章的 Agent 不会把它们当建议用。' % BY_KIND.get('incomplete_condition', 0)),
]

CONTENT['5.3'] = [
    ('p', '映射表分两层。第一层是目标映射：条件建议要挂到具体对象上（哪个景点、哪条路、哪种交通），'
          '挂不上对象的留 target_id 为空，标成"景区级未定位"，不强行归入某个景点。'
          '第二层是类别映射：%d 条条件子句按受控类别落位，日期时段最多（%d），'
          '其次是"其他"（%d）——这一类里多是"人多的时候""第一次来"这种没法归类的说法。' % (
              sum(1 for r in RULES for _ in ((r.get('condition') or {}).get('clauses') or [])),
              CAT_TOTAL.get('date_time', 0), CAT_TOTAL.get('other', 0))),
    ('tbl', '表5-3  条件子句的类别分布',
     ['类别', '子句数', '典型原文'],
     [[CAT_NAME[k], str(v), SAMPLE[k]] for k, v in sorted(CAT_TOTAL.items(), key=lambda kv: -kv[1])],
     [3.6, 2.0, 9.0]),
    ('p', 'force 字段记录说话人的确定程度，按"语气"理解：'
          '%d 条里"推荐"最多（%d），"必需"只有 %d 条，"禁止"和"不建议"加起来 %d 条。'
          '这个分布符合游记的写法——大部分是"建议你怎么样"，真正硬性的规定（禁止带食物、'
          '必须提前订票）很少，而且基本都出自官网那几篇。' % (
              len(RULES), BY_FORCE.get('recommended', 0), BY_FORCE.get('required', 0),
              BY_FORCE.get('prohibited', 0) + BY_FORCE.get('avoid', 0))),
    ('p', '去重按字面执行：仅当目标与条件相同、且规范化文本完全一致时才合并，'
          '结果 %d 条规则并成 %d 个条目，只有 %d 个条目来自两条以上的规则。'
          '原因不是流程有问题，是语料里几乎没人用一模一样的措辞说同一件事：'
          '"需要雨衣"和"记得带雨衣"是一回事，但按字面不算重复。'
          '近义合并需要语义判断，本次未实现，仅在 outputs/P5/校验/去重与近重复候选.json 中'
          '保留候选清单。' % (len(RULES), len(ITEMS),
                        sum(1 for i in ITEMS if len(i['source_record_ids']) > 1))),
    ('p', '目标映射的粒度也记了一笔：%d 条观察里，引用范围落在段内提及的 %d 条、'
          '落到景区实体表的 %d 条、落到整个输入包的 %d 条。'
          '最后那 %d 条只能说明"这篇文档提过"，不能说明"这个景点适用"，后面 P7 取建议时会优先用前两类。' % (
              P5.get('active_completed_observations', 0),
              (P5.get('reference_scopes') or {}).get('local_mention', 0),
              (P5.get('reference_scopes') or {}).get('scene_registry', 0),
              (P5.get('reference_scopes') or {}).get('in_package', 0),
              (P5.get('reference_scopes') or {}).get('in_package', 0))),
]

CONTENT['5.4'] = [
    ('p', '人工核对按陈述类型分层抽取 10 条：六种类型各取一条，另加 4 条随机，随机种子固定，'
          '结果可复现。逐条回原文比对三项：内容是否一致、条件是否遗漏、语气是否夸大。'
          '明细写在 outputs/P5/校验/人工核对10条.csv，含规则 ID 与原文片段，可逐条复核。'),
    ('p', '另生成 50 条分层核对底表：按景区与类型分层、种子固定，每条并排给出抽取结果与原文引句，'
          '判断列（内容一致、条件无遗漏、语气相符、总体判定）待人工填写，填写后由汇总脚本输出合格率与不合格明细。'),

    ('tbl', '表5-4  10 条分层核对',
     ['类型', '抽取结果', '原文', '判断'],
     [['条件建议', '排了40米的 → 不必坐25米的', '雨林升降塔40米(排了40米的就没必要坐25米的了)', '合格'],
      ['条件事实', '跟秋游大军错开 → 优速通没用上', '海洋王国去其实我们都没等过(跟秋游大军错开玩了)', '合格：条件是事后归因'],
      ['一般建议', '住熊猫酒店结束后步行回', '住熊猫酒店结束后直接步行回。', '合格'],
      ['条件限制', '刚装修完 → 没选企鹅酒店', '没有选择企鹅的原因很简单,刚装修完没多久', '不合格：个人取舍写成通用限制'],
      ['条件不完整', '20:33 黄龙九寨（或 20:40 松潘）→ 空', '20:33黄龙九寨(或者20:40松潘)C5758→22:20成都东', '合格：正确标为不完整'],
      ['一般限制', '不可喂食', '可开车窗,不可喂食,中途不可下车', '合格：丢了"自驾"场景限定'],
      ['一般建议', '10:30 看百鸟飞歌', '10:30 准时看百鸟飞歌(超震撼)', '合格'],
      ['条件事实', '秋游大军十点来、三四点走 → 同文', '不过她们一般十点来,三四点就走了', '不合格：条件与结论同文'],
      ['一般建议', '观光车最省心', '观光车最省心~', '合格：周边有 OCR 噪声'],
      ['条件建议', '带 1 岁以内宝宝 → 选企鹅酒店', 'Tips5:如果带着1岁以内的宝宝来长隆还是建议选择企鹅的', '合格']],
     [2.4, 4.2, 5.4, 2.6]),
    ('p', '10 条中 8 条与原文一致，2 条不合格，失败方式不同。'
          '一条是类型判断越界：作者写"没选企鹅是因为刚装修完"，这是个人取舍的因果解释，'
          '被抽成 force=avoid 的通用限制，语气被抬高。'
          '另一条是结构冗余：条件与结论来自同一句话（"秋游大军十点来、三四点走"），'
          '展开后成了"如果 X 则 X"，没有新增信息。'
          '两类都说明问题不在抄录，而在判定：什么算条件、什么算建议，规则层给不出更细的约束。'),
    ('p', '另有 %d 条被标为 incomplete_condition，多为段落截断或原文本身只写了一半。'
          '这类没有硬补，单独留在库里，第 7 章的 Agent 不把它们当建议用。'
          '抽查中 1 条（第 5 条）属于此类，判定正确，说明"条件完整度"字段在截断场景下可用，'
          '但不能反过来当作内容正确的证据。' % BY_KIND.get('incomplete_condition', 0)),
    ('p', '核对过程还暴露三处细节问题。一是标点被规范化（全角逗号变半角），'
          '按原文精确匹配会失败，核对脚本要先做标点归一。'
          '二是口语词被删（"就不建议"→"不建议"），读起来更整齐，但严格说改动了原文。'
          '三是时态类事实（"4 月 1 号已开放"）被抽成无条件事实，'
          '库里统一标了 current_validity_verified=false，使用时需复核年份。'),
    ('p', '10 条样本不足全量的百分之一，不能据此推出整体准确率。'
          '若要给出准确率，需按类型分层抽取 100 条以上、由两人独立判定并计算一致性，'
          '本次实验未覆盖，因此本节只报告个案层面的核对结果。'),
]

CONTENT['5.5'] = [
    ('p', '六种陈述类型在三个景区都以"一般建议"为主：广州 %d 条、珠海 %d 条、九寨沟 %d 条，'
          '分别占各景区规则的 %d%%、%d%%、%d%%。条件类（条件建议与条件事实合计）在珠海占比最高，'
          '与海洋王国攻略的写法有关：身高限制、天气影响、项目时长多以条件句给出。' % (
              *[BY_KIND_SCENE[s]['general_advice'] for s in ('GZCL', 'ZHCL', 'JZG')],
              *[round(BY_KIND_SCENE[s]['general_advice'] / sum(BY_KIND_SCENE[s].values()) * 100)
                for s in ('GZCL', 'ZHCL', 'JZG')])),
    ('fig', 'P5/图表/图5-1_陈述类型与景区.png',
     '图5-1  六种陈述类型在三个景区的分布', 14.4),
    ('p', '条件子句的类别分布见左图：日期时段 %d、其他 %d、人群 %d，三类合计占 %d%%。'
          '"其他"一类占比高于预期，回查原文多为"人多的时候""第一次来的话"'
          '这类带着条件形式但没有受控类别的说法——说明该类别表覆盖不足，'
          '至少缺少"经验/次数"一类。人群条件做了关键词粗分（右图）：带小孩 %d、其他 %d、'
          '住店客人 %d、带长辈 %d。粗分仅用于描述统计，不作为判定依据，'
          '因为原文写法分散——"小盆友""特种兵""恐高的姐妹"都不是标准说法，关键词难以覆盖。' % (
              CAT_TOTAL.get('date_time', 0), CAT_TOTAL.get('other', 0), CAT_TOTAL.get('audience', 0),
              round(sum(CAT_TOTAL.get(k, 0) for k in ('date_time', 'other', 'audience'))
                    / max(1, sum(CAT_TOTAL.values())) * 100),
              AUD_BUCKET.get('带小孩', 0), AUD_BUCKET.get('其他人群', 0),
              AUD_BUCKET.get('住店客人', 0), AUD_BUCKET.get('带长辈', 0))),
    ('fig', 'P5/图表/图5-2_条件类别与人群.png',
     '图5-2  条件子句类别（左）与人群条件粗分（右）', 14.4),
    ('tbl', '表5-5  分景区的规则数',
     ['景区', '规则数', '占比', '备注'],
     [[SCENE_CN[sc], str(sum(BY_KIND_SCENE[sc].values())),
       '%.1f%%' % (sum(BY_KIND_SCENE[sc].values()) / len(RULES) * 100), note]
      for sc, note in (('GZCL', '官网 3 篇贡献了大部分硬性规定'),
                       ('ZHCL', '条件类比例最高'),
                       ('JZG', '多为路线与时间安排'))]
     + [['合计', str(len(RULES)), '100%', '—']],
     [3.0, 2.4, 2.0, 7.2]),
    ('p', '还有 %d 条规则被标成"条件完整"（p5_complete），占 %0.0f%%。'
          '这个比例不高，主要因为大量建议本来就无条件——"早点去排队"不需要前提。'
          '把 complete 理解成"条件和结论都齐了"更准确，它不衡量建议本身有没有用。' % (
              COMPLETE, COMPLETE / len(RULES) * 100)),
]
