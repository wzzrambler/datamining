# P3全自动路线产物

由P1全文、P2正式v2和来源映射直接生成，不依赖预先定位配置。

- routes.json：自动发现的路线、原文证据、P2引用、先后关系和时间层级。
- route_steps.csv：步骤平面表。
- candidate_inventory.json：每篇扫描信号、接纳/拒绝/重复抑制及原因。
- route_compare.json：同景区、范围和粒度的全部合格自动配对。
- summary.json：本次统计；diagnostics.json：时间冲突和未知标准名。
- validation.json：位置/引用/层级检查；run_manifest.json：输入、代码、产物哈希及全自动标记。

正式入口：src/P3路线层级挖掘/run.py。规则判断尚无独立语义准确率；未输出路线不等于原文无路线。
