# -*- coding: utf-8 -*-
# 领域词表：补 jieba 切不开的复合专名。
#
# 项目2 的词典是手写的，收不全是正常的，所以这里再扫一遍正文自动补。
# discover() 给项目2 当词典用，只收出现两次以上的合成词，
# 只出现一次的噪声太多（"从鲸鲨馆""看看天鹅"也会被合成）；
# merge_spans() 给项目4 造候选，共指只要候选够全，不受词频限制，
# 相邻词能拼就拼，"黑颈天鹅"这种只出现一次的词也有机会进列表。
# 两个函数共用一份中心词表，改一处两边一起变。

import re

# 中心词：复合专名的尾巴。这些字单独出现时几乎都是某个地点的一部分
HEADS = ["天鹅", "山地", "森林", "草原", "广场", "瀑布", "湖", "海", "馆", "园", "岛", "山"]

# 设施：跟在景点名后面组成"天鹅湖缆车"这类
FACILITY = ["缆车", "小火车", "观光车", "栈道", "剧场", "影院", "酒店", "餐厅"]

# 前缀是这些词时不合成：它们是动词、方位或时间，不是修饰语
STOP = set("""的 了 在 有 是 和 与 个 这 那 上 中 下 里 外 前 后 到 去 看 来 走 玩
很 也 就 都 要 会 能 把 被 给 对 而 之 其 不 我 你 他 它 说 做 于 以 及 或 等
再 又 最 更 已 将 让 使 由 向 往 从 但 因为 包括 前往 傍晚 听说 很多 逛逛 完
乘坐 观赏 返回 沿途 下午 上午 来到 看到 发现 就是 分布 包括 各个 两个 一个
大小 左右 世界 整个 各种 其它 其他 每个 还有 沿着 进入 出发 经过 位于""".split())

# 合成出来但明显不是景点的，单独排除，注释写清楚为什么
EXCLUDE = {
    "大草原": "泛称，不指具体地点",
    "滩瀑布": "珍珠滩瀑布被切掉前两个字",
    "海瀑布": "同上，跨词边界合成",
}

CHINESE = re.compile(r"[\u4e00-\u9fa5]{3,7}")


def scan(docs, known=()):
    """扫正文，返回 {合成词: 出现次数}，不做阈值判断。

    known 是已有的景点名，"景点 + 设施"这种写法要靠它判定前半截是不是景点。
    早先这里读的是上一轮 entities_v2.json，结果每跑一次数都在 38 和 39 之间跳，
    改成由调用方把手写词典传进来，同样的输入永远得到同样的输出。
    """
    import jieba
    from collections import Counter

    known = set(known)
    cnt = Counter()
    for d in docs:
        for p in d["paragraphs"]:
            toks = [t for t in jieba.cut(p["text"]) if t.strip()]
            for i, b in enumerate(toks):
                if b not in HEADS:
                    continue
                for back in (1, 2):
                    if i - back < 0:
                        continue
                    pre = "".join(toks[i - back:i])
                    w = pre + b
                    if not CHINESE.fullmatch(w) or len(w) > 5:
                        continue
                    if any(t in STOP for t in toks[i - back:i]):
                        continue
                    cnt[w] += 1
            for a, b in zip(toks, toks[1:]):
                if b in FACILITY and a in known:
                    w = a + b
                    if CHINESE.fullmatch(w):
                        cnt[w] += 1
    return cnt


def discover(docs, known=(), min_count=2):
    """给项目2 用的词典补充：出现两次以上、且不在排除名单里的合成词。"""
    cnt = scan(docs, known)
    out = {}
    for w, c in cnt.items():
        if c < min_count or w in EXCLUDE:
            continue
        out[w] = c
    return out


def merge_spans(tokens):
    """给项目4 用：把相邻词能拼成中心词结尾的都拼一遍，不看词频。

    返回 [(词, 是否按景点对待)]，第二个值决定打分时给不给类型分。
    """
    out = []
    for i, b in enumerate(tokens):
        if b not in HEADS and b not in FACILITY:
            continue
        for back in (1, 2):
            if i - back < 0:
                continue
            pre = "".join(tokens[i - back:i])
            w = pre + b
            if not CHINESE.fullmatch(w) or len(w) > 6:
                continue
            if any(t in STOP for t in tokens[i - back:i]):
                continue
            out.append((w, True))
    return out
