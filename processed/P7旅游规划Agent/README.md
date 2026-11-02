# P7旅游规划Agent

待开发和运行后生成。本目录当前不包含新的实验结果。

规划Agent的图谱检索输入约定：只读实体关联的 `TIME_SUMMARY` 与 `ADVICE_SUMMARY`，并使用原实体上的 `advice_themes` 主题摘要；不把零散 `FACT` 或单条 `CONDITIONAL_RULE` 直接作为规划依据。按景区和作用范围筛选，保留历史信息属性，不把旧时间、费用或政策说成当前事实。