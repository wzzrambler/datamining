# P6运行与验证工具

- publish_cleaned.py：备份旧项目快照，导入并验证正式图；--replace-dataset仅允许替换release当前版本，不修改其他dataset。
- verify_live.py：读取指定导入包，执行真实Neo4j重复导入、计数、属性读回及查询验证；显式运行时访问数据库。
- check_html_details.py：通过Edge实际点击四类节点，检查侧栏时间汇总、主题建议、搜索、缩放及筛选；无需模型Key。
- network_view.html：三个景区交互HTML使用的模板。

可视化生成入口为tools/build_p6_extra_html.py，直接从正式图谱嵌入数据，不依赖报告脚本。默认不额外导出视图JSON；需要时用--view-json-dir .test_tmp/p6_views指定调试目录。

质量处理入口为src/P6知识图谱/quality_pipeline.py，执行线路筛选、历史时间汇总、条件推荐归集和主题结果绑定；常规准备与导入入口为src/P6知识图谱/run.py。

报告生成、预览及核对脚本已删除，现有报告成稿仍保存在outputs/P6。
