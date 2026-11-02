# P4共指消解 V2

当前47篇P1＋P2 V2的自动结果：

- coreferences.json：实体/事件指代关联，schema p4-coreferences-2；保留完整候选、分项分数、拒绝原因及输入/程序哈希。
- tokenization.json：独立Jieba词性结果、全部字符偏移和词典哈希，原文不改写。
- summary.json：548条检测记录，含76条自动关联、397条未消解、4条篇章用法、71条排除项；不是人工准确率。
- validation.json：结构、先后与来源连续性检查，不是语义准确率。
- reproducibility.json：57项测试、7份自动产物与3份评估产物字节级重放一致，373份受保护文件未变，58060个token逐词回切通过。

运行python "src/P4共指消解/run.py"重建前四项；python "tools/verify_p4_v2.py"重建复现核验。自动预测不读人工答案。

P6读取status=resolved的antecedents数组；单项兼容antecedent，多项时兼容标量为null；未消解数组为空。位置和来源必须保留，普通名词不自动成为跨文档标准实体。实现与限制见src/P4共指消解/README.md，固定样本评估见outputs/P4/评估，分阶段对照见outputs/P4/改进对照。
