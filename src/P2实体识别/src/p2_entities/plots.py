"""非交互、可导出的P2词频柱状图；布局不依赖浏览器。"""
from collections import Counter
from pathlib import Path


def plot_frequencies(rows, folder):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    installed = {f.name for f in font_manager.fontManager.ttflist}
    candidates = ['Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC', 'WenQuanYi Micro Hei']
    font = next((x for x in candidates if x in installed), '')
    plt.rcParams['font.sans-serif'] = [font] if font else ['DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    plt.rcParams['svg.fonttype'] = 'path'
    names = {'GZCL': '广州长隆', 'ZHCL': '珠海长隆', 'JZG': '九寨沟'}
    files = []

    def bars(ax, data, title):
        top = data.most_common(12)[::-1]
        # 无中文字体时使用编号，准确名称仍保存在frequencies.json与图例映射。
        labels = [x[0] if font else f'#{i + 1}' for i, x in enumerate(top)]
        values = [x[1] for x in top]
        ax.barh(labels, values, color='#287b8e', height=0.7)
        ax.set_title(title, fontsize=12)
        ax.set_xlabel('提及次数' if font else 'Mention count')
        ax.spines[['top', 'right']].set_visible(False)
        ax.grid(axis='x', alpha=.15)
        ax.set_axisbelow(True)
        for i, count in enumerate(values):
            ax.text(count, i, f' {count}', va='center', fontsize=8)
        ax.margins(x=.15)
        return [{'label': label, 'name': item[0], 'count': item[1]} for label, item in zip(labels, top)]

    legends = {}
    fig, axes = plt.subplots(3, 1, figsize=(8, 10.5), layout='constrained')
    for ax, label in zip(axes, ('POI', 'TRANSPORT', 'TIME')):
        count = Counter()
        for r in rows:
            if r['type'] == label:
                # 全局POI不因同名跨景区而混合。
                key = names[r['scenic_code']] + '·' + r['name'] if label == 'POI' else r['name']
                count[key] += r['mention_count']
        legends[label] = bars(ax, count, label)
    for extension in ('png', 'svg'):
        path = Path(folder) / ('entity_frequency.' + extension)
        fig.savefig(path, dpi=160)
        files.append(path.name)
    plt.close(fig)
    fig, axes = plt.subplots(3, 1, figsize=(8, 10.5), layout='constrained')
    for ax, code in zip(axes, names):
        count = Counter()
        for row in rows:
            if row['scenic_code'] == code and row['type'] == 'POI':
                count[row['name']] += row['mention_count']
        legends[code] = bars(ax, count, names[code] if font else code)
    for extension in ('png', 'svg'):
        path = Path(folder) / ('poi_by_scenic.' + extension)
        fig.savefig(path, dpi=160)
        files.append(path.name)
    plt.close(fig)
    from .config import write_json
    write_json(Path(folder) / 'figure_legends.json', legends)
    return {'font': font or 'DejaVu Sans (numbered labels)', 'files': files,
            'frequency': '提及数；文档频次另见frequencies.json'}


def plot_version_comparison(counts, folder):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    fonts = {f.name for f in font_manager.fontManager.ttflist}
    font = next((n for n in ['Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC'] if n in fonts), '')
    if font:
        plt.rcParams['font.sans-serif'] = [font]
    labels = ['POI', 'TRANSPORT', 'TIME']
    fig, ax = plt.subplots(figsize=(9, 5), layout='constrained')
    for offset, version, color in [(-.19, 'v1', '#8799a3'), (.19, 'v2', '#287b8e')]:
        values = [counts[version].get(k, 0) for k in labels]
        bars = ax.bar([i + offset for i in range(3)], values, width=.36, label=version, color=color)
        ax.bar_label(bars, padding=3)
    ax.set_xticks(range(3), labels)
    ax.set_ylabel('提及次数' if font else 'Mentions')
    ax.set_title('v1/v2实体提及数（数量变化不等于准确率）' if font else 'Entity mentions (not accuracy)')
    ax.spines[['top', 'right']].set_visible(False)
    ax.set_ylim(0, max(v for c in counts.values() for v in c.values()) * 1.15)
    ax.legend(frameon=False)
    for extension in ('png', 'svg'):
        fig.savefig(Path(folder) / ('version_counts.' + extension), dpi=160)
    plt.close(fig)
