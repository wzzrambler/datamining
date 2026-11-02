# 微信公众号与景区官方采集及原清洗

config/sources.json相对脚本定位；原始数据在仓库data/微信公众号与景区官方，手动快照路径已同步。

- crawler.py：联网采集，支持--help；本次迁移未执行。
- extractors.py：采集与重建共用的站点抽取模块。
- rebuild_from_archive.py：离线重建raw_documents.jsonl，会写回原始文本导出与盘点表，仅需要重新抽取时运行。
- clean.py：原组清洗，独立输出processed/P1爬虫数据清洗/微信公众号与官网独立清洗，供对照，不覆盖正式47篇。

可在任意目录以脚本完整路径运行。原P1—P7源码、标注与结果另存archive。本轮只调整路径，未重写其算法。
