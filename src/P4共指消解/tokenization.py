"""Isolated dictionary POS tagging with lossless Python character offsets."""
import hashlib
import json
import logging
from pathlib import Path
import jieba
import jieba.posseg

jieba.setLogLevel(logging.WARNING)


class Tagger:
    def __init__(self, mentions, config):
        self.words = {}
        for typ, words in config['nouns'].items():
            for word in words:
                self.words[word] = 'nr' if typ == 'person' else 'n'
        for m in mentions:
            if m['type'] in {'POI', 'TRANSPORT'}:
                self.words[m['text']] = 'ns' if m['type'] == 'POI' else 'n'
        self.tokenizer = jieba.Tokenizer()
        cache = Path(__file__).resolve().parents[2]/'.test_tmp/p4_jieba'
        cache.mkdir(parents=True, exist_ok=True)
        self.tokenizer.tmp_dir = str(cache)
        self.tokenizer.initialize()
        for word, tag in sorted(self.words.items()):
            self.tokenizer.add_word(word, freq=1000000, tag=tag)
        self.pos = jieba.posseg.POSTokenizer(self.tokenizer)

    def cut(self, text, offset=0):
        rows, cursor = [], 0
        for pair in self.pos.cut(text, HMM=False):
            end = cursor + len(pair.word)
            if text[cursor:end] != pair.word:
                raise ValueError('Jieba token与原文不连续')
            rows.append({'start': offset+cursor, 'end': offset+end,
                         'text': pair.word, 'pos': pair.flag})
            cursor = end
        if cursor != len(text):
            raise ValueError('Jieba未完整覆盖原文')
        return rows

    def metadata(self):
        if hasattr(self, '_metadata'):
            return self._metadata
        self._metadata = {'version': jieba.__version__, 'HMM': False,
                'dictionary_sha256': hashlib.sha256(Path(jieba.__file__).with_name('dict.txt').read_bytes()).hexdigest(),
                'injected_words_sha256': hashlib.sha256(json.dumps(self.words, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                'injected_words': len(self.words)}
        return self._metadata
