# 景区游览指南的程序性知识挖掘

第一轮目录迁移：2026-10-31。正式P1包含三景区47篇（原17篇+小红书30篇），表情清理重建后共100222字符、4820段。P2已整理唯一正式实现和结果，定性审核意见按项目规范核对；完整独立金标准及P/R/F1未完成。P3已改为全文全自动路线解析、时间层级与自动配对；P4已完成Jieba候选与事件规则V2、全量运行及固定样本核验，覆盖仍有限。2026-11-01：P5完成2226原始包真实抽取及恢复、时间汇集，正式结果full-final-20261101。P6完成图清理，最终6763节点9824关系真实导入Neo4j，重复导入不增长、属性读回一致（清理前中间态7117/9311）；P7待开发。P5、P6共90项测试通过，语义质量仍有待复核项。旧实现和结果已归档，不能作为新语料的分析结果。2026-11-03：P1补图片复读剔除，截图里与作者正文重复的行按正文比对丢弃，`tools/P1/audit_ocr_repeat.py` 实测271行、占图片行8.1%，冻结产物尚未按新规则重跑；P5对每个被引用ID记录原文支撑范围（local_mention / scene_registry / in_package），并汇总进各次运行的 summary.json；P6的重复导入、节点读回、剔除线路三项改成实测后再写结论。

旧17篇的正式副本已统一全角ASCII、段落及派生统计，两处负删减量修正为0；历史原件保持不变。noise_char_removed表示非负净缩减量，句数统一识别中英文句末标点、分号及换行。

## 目录

- data/：原始证据；小红书爬虫.zip为Git LFS归档，微信公众号与景区官方/保留原data/raw。
- processed/P1爬虫数据清洗/：正式JSON/CSV/Excel/段落表；辅助资料/保存OCR缓存、评论、段落来源、编号及legacy_documents.json历史17篇基线；质量检查/保存统计和对比。
- processed/P2实体识别/：当前47篇的正式实体、词频、质量检查和候选；P3已有42条自动路线、320步；P4已有全量规则V2结果；P5正式结果为runs/full-final-20261101，P6同名快照已有真实导入验证；P7尚无新结果。
- src/P1爬虫与数据清洗/：两来源子项目，配置跟随各自代码。
- src/P2实体识别/至P7旅游规划Agent/：每阶段独立代码、配置及README。
- src/paths.py：仓库路径与安全解压，不启动实验。
- tests/P1/至tests/P6/：各阶段离线回归入口；P5/P6合计62项，已接入CI。
- outputs/：分阶段展示与报告；历史成果/单独保留旧P1—P7图表和报告。
- tools/：正式数据验证，后续报告及打包工具。
- archive/：重构前完整zip、原源码、原阶段产物。
- .cache/：本地解压缓存，不提交Git。

没有统一run_all入口，也没有根config目录。各阶段独立运行，路径相对配置文件或仓库根定位。

## 安装与Git LFS

推荐Python 3.11及以上。在仓库根目录执行：

```powershell
python -m pip install -r requirements.txt
git lfs install
git lfs pull
```

data/小红书爬虫.zip约289.83MiB，超过GitHub普通文件100MiB上限，.gitattributes已指定Git LFS。提交者需安装Git LFS后再git add/commit。本轮未提交或推送。SHA-256见同名manifest。原始文件不改写，解压后读取时映射历史路径。

## P1运行

阶段二输入：processed/P1爬虫数据清洗/documents.json。字段沿用原组；content组合正文、超过30有效字符的逐图OCR和作者评论，paragraphs在最终正文上重新定位。

```powershell
python tools/verify_p1.py
python tools/quality_p1.py --deep
python -m unittest discover -s tests/P1 -v
```

上述检查无需浏览器、Docker；不加--deep时无需大归档。verify_p1或quality_p1加--deep会安全解压本地zip并检查原始证据，仍不联网。质量检测材料位于processed/P1爬虫数据清洗/质量检查/自动质检，人工抽查表须另行填写。

离线重建正式47篇（复用已保存OCR缓存）：

```powershell
Set-Location 'src/P1爬虫与数据清洗/小红书爬虫与数据清洗/数据清洗'
python -X utf8 -m src --config config.production.json
```

首次自动解压本地zip。只有显式添加 --ocr live 才访问配置的HTTP OCR服务。公众号/官网入口见其子目录README；旧清洗输出独立，不覆盖47篇。

## P2运行

```powershell
python "src/P2实体识别/run.py"
python -m unittest discover -s tests/P2 -v
```

使用已冻结的领域词典与时间规则；无需联网、Jieba、浏览器或OCR服务。以上命令写入processed/P2实体识别与outputs/P2/图表；加--no-plots可跳过绘图。只保留最新实现及结果，必要的迭代汇总留在报告。POI用poi_role区分游览项目与通勤配套；活动另存候选。规则、配置及人工核对方式见src/P2实体识别/README.md。

## P3运行

```powershell
python "src/P3路线层级挖掘/run.py"
python -m unittest discover -s tests/P3 -v
```

读取P1全文、P2 v2实体和段落来源，自动确定路线及步骤边界、时间层级、重复表示和官方/游客配对。没有预设doc_id、段号、原句或配对配置。新输入只需P1/P2相互对应，不冻结语料定位。输出在processed/P3路线层级挖掘，报告和图表在outputs/P3；实时统计见summary.json。规则仍有误识别/漏识别，完整候选决策日志可追查，不声称人工准确率。

任务三报告已在P2 Word底稿上完成替换，交付与核对说明见[outputs/P3](outputs/P3/README.md)。报告修订正文与程序自动生成摘要分开保存；P1/P2正文及原图表保留，P4以后仍为历史章节。

## P4运行

```powershell
python "src/P4共指消解/run.py"
python -m unittest discover -s tests/P4 -v
python "src/P4共指消解/evaluate.py"
```

自动消解仅读取P1全文、P2 V2及来源映射，不读取人工答案；V2使用Jieba 0.42.1词性补充候选，最终消解仍用规则，无共指模型。输出位置、候选日志、规则参数和局限见[src/P4共指消解](src/P4共指消解/README.md)。人工标注校验与评估另行执行，只使用confirmed/user_confirmed且可判定的标注；当前五篇为已接触样本核验，未声称独立泛化准确率。V2样本输出14条关联、正确11条；事件正确4/14，不能只看已输出关联的正确率。阶段对照入口为compare.py，完整重放核验入口为tools/verify_p4_v2.py。P3 Word中的P4章节尚未更新。

## OCR与登录状态

OCR C++/Docker本体仍是独立项目，仓库仅有客户端、接口说明和324份成功缓存。已验证本机地址http://localhost:18080；串行请求，整个multipart≤10MiB、1600万像素，读取250秒；失败停止。

迁移未复制.profiles、.browser-profile、.state、Cookie文件，也未重新登录或运行爬虫。外部原小红书项目保留；如需复用原登录态、停止锁和累计预算，见爬虫README的XHS_WORKSPACE说明。

## 当前边界

- GZCL-NOTE-11缺标题，原组部分日期和采集时间缺失，如实保留。
- OCR仍可能有错字、招牌噪声及断行；长度达标不是语义质量保证，本轮不改变清洗算法。
- 原组P2—P7、图表和报告是历史对照；当前P2已做定性审核与v2迭代，不宣称独立人工准确率，P3已改为全文全自动路线解析、时间层级与自动配对；P4已完成Jieba候选与事件规则V2、全量运行及固定样本核验，覆盖仍有限；P5全量抽取/恢复和P6真实Neo4j导入已完成，仍有语义待复核项；P7待开发。
- CI运行Ubuntu/Python3.11的语法、P1—P6离线测试和P1全量质检，不下载大LFS文件，不运行浏览器、真实OCR、模型API或Neo4j服务。P7目前仅纳入语法检查。
- CI由推送main、创建/更新PR或手动运行触发；同一引用的新运行取消旧运行，单任务最长20分钟。依赖安装成功后，即使某项检查失败，仍执行其余检查并保留失败结论；P1质检报告可在Actions的Artifacts下载，保留14天。
- P1正式数据与生产清洗配置共同使用已提交的`processed/P1爬虫数据清洗/辅助资料/legacy_documents.json`作为17篇历史基线；缺失、数量不符或规范化正文不一致均报错。普通CI不依赖仓库外历史目录；`verify_p1.py --deep`额外核对本地小红书归档和原始证据存在性，不校验已移出仓库的旧实验归档。
- 原始归档含网页参数和用户公开信息，目录迁移不等于脱敏。

迁移基线与校验见archive/迁移说明.md。

P4报告已基于P3底稿完成4.1—4.5替换，交付于[outputs/P4](outputs/P4/README.md)。P1—P3正文及原图表保持，P5以后仍为历史章节；原P3版Word不覆盖。

## P5 / P6交付入口

正式资料、章节Markdown与本地复核指令见[P5P6交付索引](P5P6交付索引.md)。P5/P6已合入Word，最新成稿见[outputs/P6](outputs/P6/README.md)的P6汇总更新版。开发日志、旧版P6报告、重复视图JSON及历史验收材料归档到仓库外私人资源目录；正式结果离线重建不依赖归档。
