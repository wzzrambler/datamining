# 原始数据与来源说明

- `raw_documents.jsonl`：17 份未清洗正文，数据集的输入。可以由存档离线重解析得到，不需要联网。
- `source_archive/raw_html/`、`source_archive/原始网页索引.csv`：本次随工程提交的原始网页存档，19 个页面。
- `archive_inventory.csv`：19 个页面逐条盘点，记录解析标题、正文字数、纳入还是排除，以及排除理由。
- `crawl_log.csv`：17 份数据的归档状态表。**它不是当时的 HTTP 请求日志**，请求时间、重试次数没有原始记录，表里只保留仍在 jsonl 中的状态码与来源说明。
- `manual/*.txt`：两份九寨沟正文快照。它们来自检索服务返回的正文，原始 TXT 不在网页存档里，这个限制在文件名对应的来源说明中写明。

## 离线复现

```bash
python src/rebuild_from_archive.py   # 从存档重解析出 raw_documents.jsonl（不联网）
python src/clean.py
python src/quality_check.py
python src/run_all.py                # 项目2~7 也可以一把跑完
```

## 存档盘点结论

19 个页面里 15 个对应数据集文档，4 个没有纳入：

| 页面 | 原因 |
|---|---|
| JZG-NOTE-02.html | 正文与 JZG-NOTE-01.html 完全相同 |
| ZHCL-NOTE-04.html | 正文与 ZHCL-NOTE-02.html 完全相同 |
| ZHCL-NOTE-05.html | 正文与 ZHCL-NOTE-03.html 完全相同 |
| ZHCL-OFF-03.html | 只提取到 20 个字符，页面没有正文 |

重复页没有删掉，留在存档里可以说明这几个页面确实访问过、只是没有新内容。
