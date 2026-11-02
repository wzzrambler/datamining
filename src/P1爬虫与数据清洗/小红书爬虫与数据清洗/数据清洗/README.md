# 小红书清洗与合并

在本目录执行：

```powershell
python -X utf8 -m src --config config.production.json
python -X utf8 -m unittest discover -s tests -v
```

依赖安装统一使用仓库根requirements.txt。正式配置会从本地data/小红书爬虫.zip安全解压到.cache，读取三组归档，把历史绝对路径映射到缓存。默认不联网，复用processed/P1爬虫数据清洗/辅助资料/OCR结果与缓存/cache；输出为根processed/P1爬虫数据清洗，历史17篇不修改。

## 规则与输出

- 正文清除HTML残留、网址、联系方式、表情、话题标签及不可见字符，统一空白、时间和全角字符，保留路线箭头、编号及条件词。
- 小红书方括号表情按已知名称清理（兼容R后缀），计入emoji_removed；清除残留[话题]，保留正常方括号说明及未知标记。附加文本长度在上述清理后计算。
- 作者正文不受30字门槛限制；每张图片、每条作者评论清洗后汉字/字母/数字严格>30才纳入。comment_policy=all可纳入其他用户评论，默认author_only。
- 顺序为作者正文→图片顺序→已采集评论顺序，行级去重后生成content及paragraphs。OCR断行的语义重组尚未开发。
- 最终JSON字段与原组一致，无新增字段；min_document_chars默认200，只是正文长度标准，不保证语义正确或采集完整。
- 辅助资料保留OCR原始响应、评论原记录、清洗明细、段落来源和稳定编号映射；质量检查保存统计及清洗对比。历史17篇保留原件，legacy_documents.json仅适配原始证据路径。
- 合并时对旧17篇的正式副本做一对一全角ASCII/空格转换，重建段落和派生统计，不重复删词、去重或重排；legacy_documents.json与历史文件均不改写，重建会自动重放上述适配。
- 所有文档的noise_char_removed统一为max(0,raw_char_len-clean_char_len)，表示非负净缩减量，不等于实际删除字符次数。句数统一按中英文句末标点、分号及换行切分，片段至少5字符，不按小数点切分。
- documents.csv不含正文；paragraphs.csv一行一段；Excel与JSON的正文一致，JSON为完整文本依据。输出路径相对仓库根目录。

## OCR协议与实际部署

服务本体独立维护，未复制C++源码、模型或Docker程序。真实联调已验证http://localhost:18080。只有显式添加--ocr live才访问服务：GET /health就绪后，POST /v1/ocr，multipart字段image和request_id。JPEG/PNG原字节上传，WebP本地转PNG，不做EXIF自动旋转。

客户端限制整个multipart请求体≤10MiB，1600万像素；连接5秒、读取250秒。服务CPU串行，最多接纳8个执行/排队请求，队列和计算响应分别最多120秒。HTTP错误（含504）停止本轮后续OCR，不重试。文字、confidence、box、timings_ms与memory原样存储，往返耗时单独记录；confidence不是校准概率，worker_process_peak_rss_kib是工作进程累计峰值。

成功响应按图片SHA-256和cache_namespace缓存，换模型或字典时更换namespace。已有324份真实响应可离线使用；本轮迁移未再次调用服务。服务无鉴权/TLS/CORS，未验证跨设备可达性。
