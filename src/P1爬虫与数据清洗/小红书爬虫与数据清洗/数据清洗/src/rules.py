import html
import copy
import re
import unicodedata
from collections import Counter

STAT_FIELDS = ('noise_line_removed', 'dup_line_removed', 'invisible_removed',
               'html_tag_removed', 'url_removed', 'email_removed', 'tel_removed',
               'wxid_removed', 'emoji_removed')
TOPIC = re.compile(r'#[^#\n]*?\[话题\]#|#[^#\n]+#')
INVISIBLE = re.compile('[\u200b-\u200f\u202a-\u202e\u2060\ufeff\u00ad\x00-\x08\x0b\x0c\x0e-\x1f]')
EMOJI = re.compile('[\U0001f000-\U0001faff\u2600-\u27bf\ufe0f\u200d]')
# 小红书文本导出的表情占位符；只匹配已知名称，保留正常方括号内容。
XHS_EMOJI_NAMES = (
    '微笑', '笑哭', '捂脸', '赞', '比心', '哭惹', '红色心形', 'doge',
    '飞吻', '打卡', '害羞', '偷笑', '派对', '玫瑰', '大笑', '哇', '萌萌哒',
    '失望', '石化', '举手', '抱拳', '汗颜', '棒', '斜眼', '黄金薯', '亲一个',
    '自拍', '向右', '飞机', '皱眉', '吧唧', '回招呼',
)
XHS_EMOJI = re.compile(r'\[(?:' + '|'.join(map(re.escape, XHS_EMOJI_NAMES)) + r')(?:R)?\]')
TIME = re.compile(r'\d{1,2}[:：]\d{2}|上午|中午|下午|傍晚|晚上|早上|清晨|\d+点')


def effective_len(text):
    """只统计汉字、字母、数字；按字符计数，不是中文分词。"""
    return sum(unicodedata.category(c)[0] in 'LN' for c in text)


def normalize_format(text):
    """一对一转换全角ASCII及空格，保留中文、路线箭头及换行结构。"""
    text = ''.join(chr(ord(c) - 0xfee0) if '\uff01' <= c <= '\uff5e' else c for c in text)
    return text.replace('\xa0', ' ').replace('\u3000', ' ')


def count_sentences(text):
    """中英文句末/分号及换行分段，保留原≥5字符门槛，不按小数点切分。"""
    return sum(len(s.strip()) >= 5 for s in re.split(r'[。！？；!?;\n]', text))


def normalize_legacy_documents(docs):
    """旧清洗结果的格式适配，不重复执行删词、行去重或OCR。输入不改写。"""
    result = copy.deepcopy(docs)
    for row in result:
        row['content'] = normalize_format(row['content'])
        row['paragraphs'] = paragraphs(row['content'])
        row['clean_char_len'] = len(row['content'])
        row['paragraph_count'] = len(row['paragraphs'])
        row['sentence_count'] = count_sentences(row['content'])
        # 沿用新文档口径：非负净缩减量，不是逐个字符的实际删除次数。
        row['noise_char_removed'] = max(0, row['raw_char_len'] - row['clean_char_len'])
    return result


def clean_text(raw):
    text = html.unescape(str(raw or '')).replace('\r\n', '\n').replace('\r', '\n')
    stats = Counter({k: 0 for k in STAT_FIELDS})
    for key, pattern in [
        ('invisible_removed', INVISIBLE),
        ('html_tag_removed', re.compile(r'</?[A-Za-z][^>]*>')),
        ('url_removed', re.compile(r'(?:https?://|www\.)[A-Za-z0-9\-._~:/?#\[\]@!$&\x27()*+,;=%]+')),
        ('email_removed', re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}')),
        ('wxid_removed', re.compile(r'(?:微信号|wechat|weixin)\s*[:：]?\s*[A-Za-z][\w-]{5,19}', re.I)),
        ('tel_removed', re.compile(r'(?<!\d)(?:1[3-9]\d{9}|400[- ]?\d{3}[- ]?\d{4})(?!\d)')),
        ('emoji_removed', EMOJI),
    ]:
        text, stats[key] = pattern.subn('', text)
    text = TOPIC.sub('', text)
    text = text.replace('[话题]', '')
    text, marker_count = XHS_EMOJI.subn('', text)
    stats['emoji_removed'] += marker_count
    text = normalize_format(text)
    lines = []
    seen = set()
    for line in text.splitlines():
        line = re.sub(r'[\t ]+', ' ', line).strip()
        if not line:
            continue
        if re.fullmatch(r'(?:展开|收起|查看更多|点击展开)(?:全部)?(?:\d+条)?(?:回复|评论)?', line):
            stats['noise_line_removed'] += 1
            continue
        if not effective_len(line):
            stats['noise_line_removed'] += 1
            continue
        line = re.sub(r'(\d{1,2})\s*:\s*(\d{2})', lambda m: f'{int(m[1]):02}:{m[2]}', line)
        line = re.sub(r'([！？。，、；：,.!?;:])\1{2,}', r'\1', line)
        if line in seen:
            stats['dup_line_removed'] += 1
            continue
        seen.add(line)
        lines.append(line)
    return '\n'.join(lines), dict(stats)


def paragraphs(text):
    result, pos = [], 0
    for line in text.split('\n'):
        if line:
            result.append({'i': len(result) + 1, 'start': pos, 'end': pos + len(line), 'text': line})
        pos += len(line) + 1
    return result


def shingles(text):
    text = re.sub(r'\s+', '', text)
    return {text[i:i+3] for i in range(max(0, len(text)-2))}


# 图片 OCR 与作者正文的重合判定：去掉标点后比较，能容忍 OCR 断行、
# 全角半角混用和个别错字。
REPEAT_GRAM = 4
REPEAT_COVER = 0.8
REPEAT_MIN_EXACT = 4
REPEAT_MIN_COVER = 8


def squash(text):
    return re.sub(r'\W+', '', text).casefold()


def grams(text, n=REPEAT_GRAM):
    return {text[i:i+n] for i in range(max(0, len(text)-n+1))}


def repeats_body(line, body, body_grams):
    """判断图片 OCR 的一行是否只是正文已经写过的内容。

    短行容易被 OCR 截断成正文的子串（如「海鸟世」之于「海鸟世界」），
    整行包含即可判定；长行改用 4-gram 覆盖率，允许两三个字不一致。
    """
    if len(line) < REPEAT_MIN_EXACT:
        return False
    if line in body:
        return True
    if len(line) < REPEAT_MIN_COVER:
        return False
    own = grams(line)
    return len(own & body_grams) / len(own) >= REPEAT_COVER
