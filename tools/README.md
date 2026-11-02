# 程序运行辅助工具

本目录仅保留数据处理、批量抽取、图谱发布、可视化生成及程序结果校验工具。报告生成、Word更新、报告预览及报告核对脚本已删除。

| 工具 | 用途 |
| --- | --- |
| verify_p1.py | 只读核对P1正式数据及来源；--deep额外核对原始归档 |
| quality_p1.py | 对47篇正式文本执行字段、重复和噪声质检；不请求模型 |
| verify_p4_v2.py | 核对P4共指消解重放、输入及消融结果 |
| build_p6_extra_html.py | 从正式图谱生成三个景区的独立交互HTML |
| P5/run_batch.py | 分景区批量请求模型抽取，支持断点 |
| P5/recover_packages.py | 拆分恢复失败包、检查覆盖并整合结果 |
| P6/publish_cleaned.py | 备份旧快照，发布并验证清洗后的图谱 |
| P6/verify_live.py | 真实Neo4j重复导入、查询和属性读回核验 |
| P6/check_html_details.py | 使用Edge检查交互HTML的节点详情、搜索、筛选及缩放 |
| P6/network_view.html | 交互HTML模板，由生成脚本填充正式数据 |

主要算法与阶段运行入口在../src/，正式输入和结果在../processed/。P5离线结果整合使用src/P5条件建议抽取/run.py export；P6质量处理使用src/P6知识图谱/quality_pipeline.py，常规导入使用src/P6知识图谱/run.py。

HTML生成示例：python tools/build_p6_extra_html.py。默认不另存重复视图JSON，需要调试时添加--view-json-dir .test_tmp/p6_views。

批量抽取需要模型配置；数据库发布与真实验证需要Neo4j配置。生成HTML和离线数据检查无需模型Key。
