import copy
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/P6知识图谱'))
from p6_quality.llm_themes import validate,assemble,make_package,OUTPUT_TOKENS,CONTEXT_LIMIT


class LLMThemeTests(unittest.TestCase):
    def setUp(self):
        self.item={'item_id':'ITEM','condition':{'text':'下雨','logic':'single'},'text':'建议带伞',
                   'force':'recommended','original_statement_kind':'conditional_advice',
                   'joint_targets':[{'kind':'POI','id':'A'}],'scope':{},'sources':[],
                   'source_record_ids':['RULE-A']}
        self.group={'advice_summary_id':'ADVICE-A','target_id':'A','scenic_code':'JZG','items':[self.item]}
        self.nodes={'A':{'id':'A','label':'POI','properties':{'name':'景点A'}}}
        self.package=make_package(self.group,self.nodes)

    def test_reject_unknown_or_missing_evidence(self):
        for refs in [[],['E002'],['E001','E001']]:
            with self.assertRaises(ValueError):
                validate({'topics':[{'label':'天气','summary':'历史资料记载，雨天建议带伞','item_refs':refs}]},self.package['refs'])
        p=copy.deepcopy(self.package)
        p['refs']['E002']=self.item
        with self.assertRaisesRegex(ValueError,'缺少'):
            validate({'topics':[{'label':'天气','summary':'历史资料记载，雨天建议带伞','item_refs':['E001']}]},p['refs'])

    def test_assemble_binds_original_entity_and_source_ids(self):
        raw={'topics':[{'label':'天气与装备','summary':'历史资料记载，下雨时建议携带雨伞。','item_refs':['E001']}]}
        result=assemble(self.package,raw)
        self.assertEqual(result['entity_id'],'A')
        self.assertEqual(result['topics'][0]['source_record_ids'],['RULE-A'])
        self.assertEqual(result['topics'][0]['source_item_ids'],['ITEM'])
        self.assertFalse(result['semantic_correctness_independently_verified'])

    def test_same_entity_scoped_to_scene_and_prompt_budget(self):
        g=copy.deepcopy(self.group);g['scenic_code']='ZHCL'
        other=make_package(g,self.nodes)
        self.assertNotEqual(other['fingerprint'],self.package['fingerprint'])
        self.assertLessEqual(self.package['estimated_prompt_tokens']+OUTPUT_TOKENS+1024,CONTEXT_LIMIT)


if __name__=='__main__':unittest.main()
