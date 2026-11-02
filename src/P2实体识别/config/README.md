# 正式配置

lexicon.json、time_rules.json、policy.json、candidate_specs.json共同控制当前识别行为。decisions.json与changes.json记录已采用的边界和补词依据；词条带原文证据。

manifest.json保存输入与配置文件SHA256，运行时检查；其中v2仅为本次已验收修订版的元数据标识，不依赖旧版本文件。quality_cases.json保存39项正式定点验收断言，acceptance.json保存已验收实体文件哈希，供回归测试验证精简后结果不变。

请勿仅修改JSON而不重新核验配置和结果；更换语料或调整规则需要重新验收。配置中保留的历史哈希和决策说明不是可运行旧版程序。
