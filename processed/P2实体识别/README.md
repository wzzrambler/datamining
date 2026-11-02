# P2正式结果

只保留最新一套结果，共47篇、3647次提及。

- entities.json：正式实体、角色、位置与来源；后续阶段的主要输入。
- frequencies.json：提及次数、文档频次和原始写法。
- summary.json、validation.json：数量统计和结构校验。
- quality_check.json：39项定点验收，不是人工准确率。
- run_manifest.json：输入、配置、程序及输出哈希。
- 辅助资料/：歧义诊断、活动、类型未明和省略展开候选，不计入正式实体频次。

正式入口为src/P2实体识别/run.py，报告与图表在outputs/P2。早期预测、试错样本及分版本目录已从项目移除；不把定性意见填成完整gold。
