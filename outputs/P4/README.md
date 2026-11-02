# P4 样本与人工标注草稿

自动算法已实现；本目录的原文JSON与人工标注CSV由人工标注流程维护，自动消解程序不覆盖它们。当前标注状态以CSV实际值为准。

新增自动输出：

- `P4自动消解检查表.csv`：完整自动预测、未消解原因及基线。
- `P4消解文本_仅供检查.json`：原文附注版，主要产物仍为processed/P4共指消解/coreferences.json中的关联。
- `P4自动结果说明.md`：全量统计和限制。
- `评估/`：标注格式校验、已确认行的规则/基线对比、严格位置匹配明细及可读核验说明。当前是五篇已接触样本核验，不是独立泛化准确率。

入口、依赖、参数与完整方法见`src/P4共指消解/README.md`；以下保留人工样本格式说明。

- `P4样本原文与段落.json`：正式P1中选出的5篇游记全文、段落边界与来源映射。`content`逐字符保留P1值；每篇含UTF-8编码后的`content_sha256`，顶层另含源JSON字节哈希。
- `P4人工标注样本.csv`：每行一个指代表达或扫描误命中。候选扫描命中标`auto_detected`；人工通读全文后补入的表达标`manual_added`。两类都需人工核验。
- CSV由标准CSV库写入，UTF-8 BOM；`antecedents_json`为标准JSON数组。缺失位置为空，读取为可空整数，不用0或-1。
- 偏移为Python Unicode字符索引，零起点、左闭右开。`reference_text`、`sentence_text`、`context_text`必须等于正文对应区间切片。`reference_expression`补充完整指代表达。
- `is_target=yes`时，`gold_status`为`antecedent_found`、`no_clear_antecedent`、`non_entity_reference`或`unjudgeable`；`is_target=no`时状态留空，排除原因在备注；`uncertain`不进入正式评估。
- `no_clear_antecedent`表示意思可理解但无可定位先行内容；`unjudgeable`表示人工也不能可靠判定。`non_entity_reference`可以有事件/篇章区间，也可以留空并说明理由。
- 类型使用受控值：`person/animal/location/facility/transport/event/discourse/other`。单一先行词用标量字段；多个先行词时全部标量先行词字段（`start/end/text/type`）留空，以JSON数组保存每项`start/end/text/type`。
- 助手语义标注已由用户逐项核验，CSV中`review_status=user_confirmed`；五篇样本的参考答案可用于后续样本评估。人工通读完整五篇，不受算法60字窗口约束；人工补入项单独标来源。
- 当前记录51条：自动扫描35条、人工补入16条。

## 2026-10-31 V2改进交付

人工原文JSON与CSV已冻结，本轮未修改，也不新增补标任务。新增改进对照/目录保留V1及A—D配置、样本预测、评估与哈希；评估/为当前V2严格核验。新版实体/事件通道与接口见src/P4共指消解/README.md；全词性与偏移见processed/P4共指消解/tokenization.json。

现有五篇开发样本：输出14条关联、正确11条、错误3条，事件正确4/14，候选14/33，检测39/41；不是独立留出评估。完整多先行词真实样本仍未成功。未对齐自动命中保持未知，不要求再次人工处理。

## P4实验报告交付

- 第X组_组长姓名_《数据挖掘》探索性实验报告_P1P2P3P4修订版.docx：基于P3 Word底稿，仅替换P4章的4.1—4.5；原P3底稿保留。
- P4实验报告正文.md：本次报告正文，区别于程序自动生成的运行说明。
- 报告案例核对.json：8例原文、人工区间和预测区间，以及正式输入哈希。
- 报告更新核对.json：原包成员、章节XML和Word只读排版检查结果。

P1—P3及全部前置正文原始XML完全一致，前25页渲染像素一致；P5以后原始XML也保留，仍属历史内容。全部原样式、页眉页脚和原图表保留。新版46页，P4位于物理页26—33，共9表、8例；表格整表排版、原文区间和指标核验已通过。预览PDF/PNG仅用于内部检查，不作为本次交付。
