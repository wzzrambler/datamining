import csv
import tempfile
from collections import Counter

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from .ocr import write_json
from .pipeline import FIELDS


def export(output, docs, duplicate_pairs, config, details):
    output.mkdir(parents=True, exist_ok=True)
    quality_dir = output / config.get('quality_dir_name', '')
    quality_dir.mkdir(parents=True, exist_ok=True)
    write_json(output / 'documents.json', docs)
    fields = [k for k in FIELDS if k not in ('content', 'paragraphs')]
    with (output / 'documents.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(docs)
    para_rows = [dict(doc_id=d['doc_id'], para_id=p['i'], char_start=p['start'],
                      char_end=p['end'], char_count=p['end']-p['start'],
                      scenic_area=d['scenic_area'], text_type=d['text_type'], text=p['text'])
                 for d in docs for p in d['paragraphs']]
    para_fields = ['doc_id', 'para_id', 'char_start', 'char_end', 'char_count', 'scenic_area', 'text_type', 'text']
    with (output / 'paragraphs.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=para_fields)
        writer.writeheader()
        writer.writerows(para_rows)
    wb = Workbook()
    wb.remove(wb.active)
    doc_fields = ['doc_id', 'scenic_area', 'scenic_code', 'category', 'text_type_cn',
                  'source_platform', 'source_site', 'title', 'author', 'publish_date',
                  'raw_char_len', 'clean_char_len', 'paragraph_count', 'sentence_count',
                  'has_time_expr', 'is_valid', 'quality_flags', 'source_url']
    sheets = [
        ('文档清单', doc_fields, [[d[k] for k in doc_fields] for d in docs]),
        ('正文', ['doc_id', '景区', '文本类型', '标题', '字数', '正文（清洗后）'],
         [[d[k] for k in ('doc_id', 'scenic_area', 'text_type_cn', 'title', 'clean_char_len', 'content')] for d in docs]),
        ('段落', ['doc_id', '段号', '起始', '结束', '字数', '段落文本'],
         [[p[k] for k in ('doc_id', 'para_id', 'char_start', 'char_end', 'char_count', 'text')] for p in para_rows]),
        ('字段说明', ['字段', '说明'], [[k, '含义沿用小组原数据；详见本项目 README'] for k in FIELDS]),
    ]
    excel_truncated = []
    for name, headers, rows in sheets:
        ws = wb.create_sheet(name)
        ws.append(headers)
        for row in rows:
            if any(isinstance(v, str) and len(v) > 32767 for v in row):
                excel_truncated.append([name, str(row[0])])
            ws.append(row)
        ws.freeze_panes = 'A2'
        ws.auto_filter.ref = ws.dimensions
        for row in ws:
            for cell in row:
                # 原始文本以文字写入，避免以 '=' 开头的内容被当公式执行。
                if isinstance(cell.value, str):
                    cell.data_type = 's'
                cell.font = Font(name='Microsoft YaHei', size=10, bold=cell.row == 1)
                cell.alignment = Alignment(vertical='top', wrap_text=True)
                if cell.row == 1:
                    cell.fill = PatternFill('solid', fgColor='DBEAFE')
        for column in ws.columns:
            ws.column_dimensions[column[0].column_letter].width = 24
        ws.row_dimensions[1].height = 30
    # openpyxl 写工作表会使用临时文件；限定在当前输出内，兼容受限 Windows TEMP。
    excel_tmp = output / '.xlsx_tmp'
    excel_tmp.mkdir(exist_ok=True)
    previous_temp = tempfile.tempdir
    try:
        tempfile.tempdir = str(excel_tmp)
        wb.save(output / 'dataset.xlsx')
    finally:
        tempfile.tempdir = previous_temp
        wb.close()
    counts = Counter((d['scenic_area'], d['text_type'], d['is_valid']) for d in docs)
    report = {
        'total_documents': len(docs), 'valid_documents': sum(d['is_valid'] for d in docs),
        'counts': [{'scenic_area': k[0], 'text_type': k[1], 'is_valid': k[2], 'count': v} for k, v in counts.items()],
        'missing': {k: sum(d[k] == '' for d in docs) for k in ('title', 'author', 'publish_date', 'crawl_time', 'content', 'category')},
        'duplicate_pairs_new_vs_existing_or_new': duplicate_pairs,
        'offset_errors': [d['doc_id'] for d in docs if any(d['content'][p['start']:p['end']] != p['text'] for p in d['paragraphs'])],
        'ocr_status': dict(Counter(p['ocr']['status'] for d in details for p in d['parts'] if p['kind'] == 'image')),
        'excel_truncated_cells': excel_truncated,
        'notes': ['Excel 单元格上限32767字符，完整正文以JSON为准',
                  '历史原件保留；旧文档在合并时等价转换全角ASCII、重建段落及派生统计，不重复删词；近重复仅为新文档重新计算',
                  'noise_char_removed统一为max(0,raw_char_len-clean_char_len)，表示非负净缩减量，不是实际删字符次数；句数按中英文句末/分号及换行切分',
                  '附加文本严格大于阈值；短文本保留在辅助明细中',
                  'OCR confidence 为识别得分，不是校准后的正确概率',
                  'html_tag_count 新文档填0：输入为抽取后的正文，不统计整页标签'],
    }
    write_json(quality_dir / 'quality_stats.json', report)
    examples = ['# 清洗前后对比', '', '本文件只展示新导入数据；原始 OCR 响应及评论保存在 auxiliary。', '']
    for detail in details:
        examples.extend([f"## {detail['doc_id']}", ''])
        for part in detail['parts']:
            examples.extend([f"### {part['kind']} / {'纳入' if part['included'] else '未纳入'} / 有效字符 {part['effective_chars']}",
                             '', '清洗前：', '', part['raw_text'], '', '清洗后：', '', part['clean_text'], ''])
    (quality_dir / 'cleaning_examples.md').write_text('\n'.join(examples), encoding='utf-8')
