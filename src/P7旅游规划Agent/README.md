# P7旅游规划Agent

状态：待重新开发，尚无正式实现。

目标：LLM接入、图谱工具与多场景验证。

输入以各前置阶段产物为准，基础文档为 `processed/P1爬虫数据清洗/documents.json`。本阶段代码、配置、人工标注及词典在此维护；自动生成结果写入 `processed/P7旅游规划Agent`，图表/演示写入 `outputs/P7`。原组实现保存在 archive/原实验仓库_重构前.zip 及 archive/旧版源码，不能作为当前47篇的结果。

## P6图谱检索范围

旅游规划Agent规划时只读取实体关联的两类汇总：通过 `HAS_TIME_SUMMARY` 获取 `TIME_SUMMARY`，通过 `HAS_ADVICE_SUMMARY` 获取 `ADVICE_SUMMARY`，并读取实体属性 `advice_themes` 中按景区整理的主题标签和自然语言摘要。主题摘要带有原始记录ID，可据此回查。

按景区限定查询；同一个共享交通方式在不同景区可能各有一组主题。路线片段作用域和历史时间按摘要上的作用范围使用。时间、班次、票价和政策信息都来自历史资料；未记录的时间不代表关闭，未核实的旧信息不能表述成当前实时规则。一般 `FACT` 和单条 `CONDITIONAL_RULE` 不作为规划输入。
