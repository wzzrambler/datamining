# P7 中间处理与统计

P7 从 [P6 发布清单](../P6知识图谱/release.json) 验证并导入图谱，在 PostgreSQL 中生成路线、景点、交通及摘要检索索引。会话、工具结果和行程版本保存在独立业务模式中。导入及复现命令见 [P7 使用说明](../../src/P7旅游规划Agent/README.md)。

[cache_usage.json](cache_usage.json) 记录 DeepSeek 两段用量的累计读数和区间差。单位为千 token。两段记录用于观察缓存复用情况。
