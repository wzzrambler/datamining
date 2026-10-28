# 实验项目1：景区指南数据采集与预处理

《数据挖掘》探索性实验 —— 景区游览指南的程序性知识挖掘

本目录包含项目1的全部代码、原始数据、结构化数据与质量检查产物，
可直接支撑后续项目2（实体识别）、项目3（路线层级）、项目5（条件建议）的输入需求。

---

## 1. 目录结构

```
项目1_数据采集与预处理/
├─ config/
│  └─ sources.json            采集清单：3个景区、官方路线页URL、游记检索关键词
├─ src/
│  ├─ extractors.py           站点级正文抽取规则（长隆官网 / 九寨沟官网 / 微信公众号）
│  ├─ crawler.py              采集：官网页面 + 搜狗微信检索 → 公众号文章
│  ├─ clean.py                清洗 R1~R9 + 结构化输出（CSV / JSON / XLSX）
│  ├─ quality_check.py        质量检查 Q1~Q5 + 统计图
│  ├─ pipeline.py             项目1 一键运行入口（采集→清洗→质检）
│  ├─ run_all.py              项目1~7 一键运行（默认离线）
│  ├─ rebuild_from_archive.py  从原始网页存档离线重建项目1输入
│  ├─ verify.py               报告数字与产物一致性自检
│  ├─ domain_dict.py          复合专名自动发现（项目2/4 共用）
│  ├─ p2_entities.py          项目2：三类实体识别（jieba + 词典 + 正则）
│  ├─ p3_route.py             项目3：路线解析、时间层级、官方vs游客对比
│  ├─ p4_coref.py             项目4：代词检测、规则消解与评估
│  ├─ p5_conditions.py        项目5：条件—建议抽取与分类
│  ├─ p6_graph.py             项目6：知识图谱构建与可视化
│  └─ p7_agent.py             项目7：旅游规划 Agent（ReAct 循环，5 个工具）
├─ data/
│  ├─ source_archive/         原始网页存档：19 个 HTML + 索引表
│  ├─ archive_inventory.csv   逐条记录每个页面的纳入/排除决定与理由
│  ├─ raw/
│  │  ├─ raw_documents.jsonl  原始正文，可由 rebuild_from_archive.py 离线重建
│  │  └─ crawl_log.csv        17 份数据的归档状态表（不是 HTTP 请求日志，见 data/raw/README.md）
│  └─ processed/
│     ├─ documents.csv        文档清单：元数据与统计字段，不含正文
│     ├─ documents.json       同内容 JSON + 段落偏移（后续项目直接读取）
│     ├─ paragraphs.csv       段落扁平表：一行一段，带全文起止字符位置
│     ├─ dataset.xlsx         7 个 Sheet：文档清单 / 正文 / 段落 / 字段说明 /
│     │                       清洗统计 / 景区汇总 / 规则统计
│     └─ cleaning_examples.md 清洗前后逐条对比样例
└─ outputs/
   ├─ quality_report.md       数据质量检查报告
   ├─ quality_stats.json      结构化检查结果
   └─ figures/                图1~图4
```

## 2. 环境与依赖

- Python 3.9+（实测 3.13）
- 依赖（`requirements.txt`）：

```
requests>=2.31
beautifulsoup4>=4.12
lxml>=4.9
pandas>=2.0        # 人工查看数据时使用，代码本身只用标准库 csv/json
openpyxl>=3.1      # 导出 xlsx
matplotlib>=3.7    # 生成图表
```

安装：

```bash
python -m pip install -r requirements.txt
```

## 3. 运行方式

在项目根目录执行：

```bash
python src/run_all.py                    # 项目1~7 一把跑完（离线，从存档重建）
python src/rebuild_from_archive.py       # 只用存档重解析出 17 份原始正文
python src/verify.py                     # 校验报告里的数字和产物是否一致
python src/crawler.py                  # 只采集（已采集的文档会跳过，可反复补齐）
python src/crawler.py --area JZG       # 只采集指定景区（GZCL / ZHCL / JZG）
python src/crawler.py --fresh          # 忽略已有结果，全部重采
python src/clean.py                    # 只做清洗与结构化
python src/quality_check.py            # 只做质量检查与出图
python src/pipeline.py                 # 只跑项目1（含联网采集）
python src/run_all.py --crawl           # 真的去抓网页再跑（会被限流）

# 项目2~7，按顺序跑（都只读 data/processed 里的中间产物）
python src/p2_entities.py --version v1   # 先跑一版看噪声
python src/p2_entities.py --version v2   # 加停用词后的正式版
python src/p3_route.py                   # 路线解析 + 对比，出 routes.json
python src/p4_coref.py --mode detect     # 抽代词候选
python src/p4_coref.py --mode eval       # 按人工标注评估
python src/p5_conditions.py              # 条件—建议映射表
python src/p6_graph.py                   # 建图 + 质量检查
python src/p7_agent.py                   # 七个场景跑 ReAct Agent
python src/p7_agent.py --llm             # 有 key 时改由模型决定动作
```

项目2~7 的产物都在 `data/processed/` 下，Agent 的运行记录在
`outputs/agent_runs.json` 和 `outputs/agent_demo.txt`。

输入 / 输出路径：

| 步骤 | 输入 | 输出 |
|---|---|---|
| 采集 | `config/sources.json`、互联网 | `data/raw_html/*.html`、`data/raw/raw_documents.jsonl`、`data/raw/crawl_log.csv` |
| 清洗 | `data/raw/raw_documents.jsonl` | `data/processed/documents.{csv,json}`、`dataset.xlsx`、`cleaning_examples.md` |
| 质检 | `data/processed/documents.json` | `outputs/quality_report.md`、`quality_stats.json`、`figures/*.png` |

## 4. 数据说明

### 4.1 三个景区

| 代码 | 景区 | 类型 | 官方路线来源 |
|---|---|---|---|
| GZCL | 广州长隆旅游度假区 | 主题乐园 | 长隆官网「行程推荐」栏目 |
| ZHCL | 珠海横琴长隆国际海洋度假区 | 主题乐园 | 长隆官网「行程推荐」栏目 |
| JZG | 九寨沟风景名胜区 | 自然遗产 | 九寨沟官网「智慧服务-游玩方式」 |

### 4.2 两类文本

- `official_route`：景区官方网站发布的推荐游览路线（含分站/分时段描述）。
- `travel_note`：游客在微信公众号发布的游记／攻略（含实际游览顺序与主观体验）。

### 4.3 采集方法

1. **官方页面**：`requests` 直接请求，按站点模板用 CSS 选择器定位正文容器
   （长隆 `div.c--static-flow`、九寨沟 `div.g-grid[max-text]`），见 `src/extractors.py`。
2. **游记**：以「景区名 + 游记/攻略」为关键词调用搜狗微信搜索，
   解析搜狗跳转页中 `url += '...'` 拼接出的真实 `mp.weixin.qq.com` 地址后抓取 `#js_content` 正文。
3. **反爬应对**：请求间隔 2~4 s；单会话请求数达阈值后重建 Session 并预热；
   检索结果为空或正文容器为空时自动重试（见 `SESSION_BUDGET`、`crawl_note` 的重试逻辑）。
4. **合规**：仅抓取公开可见页面，控制请求频率，数据仅用于课程实验，不作二次发布。

## 5. 清洗规则总览（详见 `src/clean.py` 的 `RULES`）

| 编号 | 处理内容 |
|---|---|
| R1 | 零宽字符、软连字符、控制字符、不间断空格/全角空格 |
| R2 | 残留 HTML 标签、HTML 实体（`&nbsp;` 等） |
| R3 | 行内多空格压缩、去首尾空白、合并空行 |
| R4 | 整行删除模板噪声：关注引导、版权声明、图片说明、购票入口 |
| R5 | 删除网址、邮箱、电话、微信号 |
| R6 | 删除 emoji；列表符号统一为 `-`；压缩重复标点 |
| R7 | 全角数字/字母转半角；`10：00` → `10:00` |
| R8 | 文档内完全重复行去重、重建段落 |
| R9 | 空值、过短、标题缺失、近重复等校验（只标记不删除） |

> 关键取舍：**不删除承载顺序与时间的信息**。`第一站（10:00）`、`【上午】`、`A→B`、
> `——` 等结构和符号全部保留，因为它们是项目3构建时间层级、项目2识别时间节点的直接依据。

## 6. 可直接复用的接口

正文只放在 JSON 里。CSV 是给人快速查看和统计用的，列少、行短；
JSON 带 `content` 和 `paragraphs`，是后面几个项目的输入。

后续项目读取 `data/processed/documents.json`，每条记录至少包含：

```python
{
  "doc_id": "GZCL-OFF-01",          # 文档唯一编号
  "scenic_area": "广州长隆旅游度假区",
  "text_type": "official_route",     # official_route / travel_note
  "content": "……清洗后正文……",       # 直接喂给分词/实体识别
  "paragraphs": [                    # 段落及其在 content 中的字符区间
      {"i": 1, "start": 0,   "end": 59,  "text": "……"},
      {"i": 2, "start": 60,  "end": 191, "text": "……"}
  ],
  "has_time_expr": 1,                # 是否含时间表达
  "clean_char_len": 2143
}
```

`paragraphs` 的区间约定：`start` 从 0 计，`end` 不含（左闭右开），
`content[start:end]` 就是该段原文。项目2 标实体位置时，先用段内偏移定位，
再换成 `start + 段内偏移`，即可得到全篇位置。

## 7. 本次运行结果（2026-10-28）

| 指标 | 数值 |
|---|---|
| 有效文档 | 17 篇（官方路线 6 / 游客游记 11） |
| 景区覆盖 | 广州长隆 3+5、珠海长隆 2+3、九寨沟 1+3 |
| 来源平台 | 景区官网 6、微信公众号 9、微信正文快照 2 |
| 原始字符数 → 清洗后 | 47143 → 46236（删除率 1.9%） |
| 删除明细 | 模板噪声行 15、文档内重复行 50、emoji 7、网址 1、电话 3 |
| 抽取阶段剥离 HTML 标签 | 15902 个 / 15 个页面（平均 1060 个/页） |
| 正文长度 | 最短 1131 / 中位数 2270 / 平均 2719.8 / 最长 6089 字 |
| 质量检查 | 三景区全部达标；无标题重复、无近重复、无乱码、无过短文档 |
| 段落切分 | 1096 段（一行一段），每段记 start/end，与正文逐字对齐 |

报告成品：`outputs/第X组_组长姓名_《数据挖掘》探索性实验报告_项目1.docx`
（另附 `outputs/报告PDF预览.pdf` 便于快速查看版式）。
报告由 `tools/fill_report.py` 生成，章节文字在 `tools/content_project1.py` 里维护，
改文字只动内容模块，排版逻辑不用碰。正文中的数字与 `outputs/quality_stats.json`、
`data/processed/dataset.xlsx` 一一对应；修改数据后重跑
`python src/pipeline.py && python tools/fill_report.py` 即可同步更新。

> 注意：如果生成时该 docx 正在 Word/WPS 中打开，脚本会因文件占用而失败。
> 此时可先关闭文档，或用 `python tools/fill_report.py --out outputs/报告.docx` 另存。

## 8. 已知问题

- 微信公众号文章由搜狗中转获取，链接带时效签名，`source_url` 中的 `timestamp/signature`
  过期后无法直接打开；复核时请以 `data/raw/source_archive/raw_html/{doc_id}.html` 为准。
- 搜狗微信检索存在 IP 级限流（累计约 30 次请求后开始返回 0 条或验证码页）。
  九寨沟有 2 篇游记在限流后无法实时抓取，改为使用检索服务返回的正文快照并留存到
  `data/raw/manual/`，在数据集中标注为「微信公众号（正文快照）」；
  这 2 篇的可复现性弱于其余 15 篇，报告中已作为数据短板说明。
- 部分官网页面（如故宫、西湖、携程、马蜂窝）在采集环境下被反爬或代理拦截，
  已放弃作为数据源，选择可稳定抓取的官网页面，详见报告 1.5 节。
- `publish_date` 对部分官网页面缺失（页面本身未标注发布时间），保留空值并做标记，
  不做填充，避免引入不实信息。

## 9. 提交包内容

压缩包 `第X组_组长姓名_《数据挖掘》实验材料_项目1.zip` 的结构：

```
│ 报告 docx                         7 个实验 + 总结反思，38 页
├─ src/                             项目1~7 的全部代码（10 个 .py）
├─ tools/                           报告排版与章节文字
├─ config/sources.json              采集清单，可据此复现抓取
├─ data/raw/                        原始数据：raw_documents.jsonl（17 篇原文）、
│                                   crawl_log.csv（抓取日志）、manual/（2 篇正文快照）
├─ data/processed/                  清洗与各项目的结构化输出
└─ outputs/                         质量报告、统计、图表、Agent 运行记录
```

`data/raw/source_archive/`（19 个页面、43 MB）没有放进主包，单独打成
`第X组_组长姓名_《数据挖掘》原始网页存档_项目1.zip`（约 9 MB）。
报告 1.5 节提到「以本地 HTML 核对」，需要抽查时解压那个包即可。

## 协作说明

三个人共改一个仓库，约定几条规矩：

- 动手前先 `git pull`，提交前 `git pull --rebase` 一下，避免无谓的合并提交。
- 报告正文的文字都在 `tools/content_*.py` 三个模块里，按章节分文件改，
  尽量不要两个人同时动同一个文件。
- 报告 docx 是二进制没法合并：谁重新生成了报告（`python tools/fill_report.py`）谁提交，
  其他人在他 push 之后再 pull，不要同时覆盖。
- 改完代码跑一遍 `python src/verify.py`，报告数字和产物对得上再提交。
- 提交信息写清楚改了哪个项目，例如「p3：路线时间层级增加耗时统计」。
