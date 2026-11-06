# 原始数据与来源说明

- `raw_documents.jsonl`：17 份未清洗正文，数据集的输入。可以由存档离线重解析得到，不需要联网。
- `source_archive/raw_html/`、`source_archive/原始网页索引.csv`：本次随工程提交的原始网页存档，19 个页面。
- `archive_inventory.csv`：19 个页面逐条盘点，记录解析标题、正文字数、纳入还是排除，以及排除理由。
- `crawl_log.csv`：17 份数据的归档状态表。**它不是当时的 HTTP 请求日志**，请求时间、重试次数没有原始记录，表里只保留仍在 jsonl 中的状态码与来源说明。
- `manual/*.txt`：两份九寨沟正文快照。它们来自检索服务返回的正文，原始 TXT 不在网页存档里，这个限制在文件名对应的来源说明中写明。

## 离线复现

在仓库根目录执行（脚本按仓库根定位数据与输出，与当前目录无关）：

```bash
python src/P1爬虫与数据清洗/微信公众号与景区官方爬虫与数据清洗/rebuild_from_archive.py
python src/P1爬虫与数据清洗/微信公众号与景区官方爬虫与数据清洗/clean.py
```

第一条从 19 个网页存档加 `manual/` 下 2 份快照重解析出 17 份 `raw_documents.jsonl` 和两张盘点表，第二条产出独立的 csv/json/xlsx 清洗结果；都不联网、不改正式 47 篇。

**注意**：这两条是"重建"而不是"只读复现"，会重写本目录下的 `raw_documents.jsonl`、`archive_inventory.csv`、`crawl_log.csv`，并新建 `processed/P1爬虫数据清洗/微信公众号与官网独立清洗/`。随包提交的原始记录里 `raw_html_path` 保留历史目录名 `data/raw/...`，重建后会改成当前目录名；要保留交付时的原始证据，请先备份再跑。验收与全库质检都不需要重建，直接用仓库根的 `python tools/verify_p1.py`、`python tools/quality_p1.py`；各阶段命令见根目录 README，本仓库没有统一的 `run_all.py` 入口。

## 存档盘点结论

19 个页面里 15 个对应数据集文档，4 个没有纳入：

| 页面 | 原因 |
|---|---|
| JZG-NOTE-02.html | 正文与 JZG-NOTE-01.html 完全相同 |
| ZHCL-NOTE-04.html | 正文与 ZHCL-NOTE-02.html 完全相同 |
| ZHCL-NOTE-05.html | 正文与 ZHCL-NOTE-03.html 完全相同 |
| ZHCL-OFF-03.html | 只提取到 20 个字符，页面没有正文 |

重复页没有删掉，留在存档里可以说明这几个页面确实访问过、只是没有新内容。
