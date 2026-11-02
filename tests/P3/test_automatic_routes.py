"""All selections must be computed from arbitrary full documents at runtime."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/P3路线层级挖掘'))
from discovery import discover_all,automatic_pairs
from parser import visit_sequence,compare
from pipeline import validate,diagnostics,digest
import pipeline


def fixture(texts,words=('甲景点','乙景点','丙景点','丁景点'),doc_id='arbitrary-uuid',kind='official_route'):
    text='\n'.join(texts);paras=[];pos=0
    for i,t in enumerate(texts,1):paras.append(dict(i=i,start=pos,end=pos+len(t),text=t));pos+=len(t)+1
    doc=dict(doc_id=doc_id,scenic_code='random-area',text_type=kind,title='格式实验',content=text,paragraphs=paras)
    mentions=[]
    for word in words:
        pos=0
        while (pos:=text.find(word,pos))>=0:
            mentions.append(dict(mention_id=f'{doc_id}-M{len(mentions)}',entity_id=word,type='POI',text=word,start=pos,end=pos+len(word),canonical_name=word,poi_role='attraction',scope='in_area',subtype='spot'))
            pos+=len(word)
    mentions.sort(key=lambda m:m['start'])
    return doc,dict(doc_id=doc_id,scenic_code='random-area',mentions=mentions)


def extract(texts,**kwargs):
    doc,e=fixture(texts,**kwargs)
    return discover_all([doc],{doc['doc_id']:e},{})


class AutomaticDiscovery(unittest.TestCase):
    def test_arrow_preserves_revisit_and_unknown_activity(self):
        routes,survey=extract(['甲景点→乙景点→未知活动→甲景点'])
        self.assertEqual(len(routes),1);self.assertEqual(visit_sequence(routes[0]),['甲景点','乙景点','甲景点'])
        self.assertEqual(len(routes[0]['steps']),4);self.assertEqual(survey[0]['candidates'][0]['status'],'accepted')

    def test_numbered_scans_entire_document(self):
        r,_=extract(['前言','第一站(10:00):甲景点','介绍','第二站(11:00):乙景点','第三站(14:00):丙景点'])
        self.assertEqual(len(r),1);self.assertEqual([s['time']['period'] for s in r[0]['steps']],['上午','上午','下午'])

    def test_new_ids_and_shifted_paragraphs_need_no_configuration(self):
        a,_=extract(['第一站:甲景点','第二站:乙景点'])
        d,e=fixture(['无关文字','其它文字','第一站:甲景点','说明','第二站:乙景点'],doc_id='entirely-different')
        for p in d['paragraphs']:p['i']+=100
        b,_=discover_all([d],{d['doc_id']:e},{})
        self.assertEqual(visit_sequence(a[0]),visit_sequence(b[0]))
        self.assertNotEqual(a[0]['steps'][0]['evidence']['start'],b[0]['steps'][0]['evidence']['start'])

    def test_tour_titles_and_day_hierarchy(self):
        r,_=extract(['一日精华游','甲景点→乙景点','二日舒适游','【Day 1】','甲景点→乙景点','【Day 2】','丙景点→丁景点'])
        self.assertEqual(len(r),2)
        two=next(x for x in r if x['title']=='二日舒适游')
        self.assertEqual([g['day'] for g in two['hierarchy']],[1,2])

    def test_unordered_period_siblings(self):
        r,_=extract(['上午——区域','甲景点 尖叫指数:五星','乙景点 尖叫指数:五星','下午——区域','丙景点 尖叫指数:五星'])
        self.assertEqual(len(r[0]['steps']),3);self.assertEqual(len(r[0]['order_relations']),2)
        self.assertTrue(all(e['after']==r[0]['steps'][2]['step_id'] for e in r[0]['order_relations']))

    def test_numbered_restart_on_new_day_keeps_day_evidence(self):
        r,_=extract(['二日舒适游','【Day 1】','第一站:甲景点','第二站:乙景点','【Day 2】','第一站:丙景点','第二站:丁景点'])
        self.assertEqual(len(r),1)
        self.assertEqual([s['time']['day'] for s in r[0]['steps']],[1,1,2,2])

    def test_summary_meal_clause_kept_in_time_hierarchy(self):
        r,_=extract(['上午甲景点→乙景点;中午未知地点吃饭休整;下午丙景点→丁景点'])
        self.assertEqual(len(r),1)
        self.assertEqual([g['period'] for g in r[0]['hierarchy'][0]['period_groups']],['上午','中午','下午'])

    def test_ambiguous_suffix_does_not_force_alias(self):
        from discovery import canonical_aliases
        d,e=fixture(['甲方海洋王国→乙方海洋王国'],words=('甲方海洋王国','乙方海洋王国'))
        aliases=canonical_aliases({d['doc_id']:e})
        self.assertNotIn('海洋王国',aliases[d['scenic_code']])

    def test_unrealized_poi_reference_kept_but_not_visited(self):
        r,_=extract(['第一站:甲景点','第二站:乙景点','第三站:没去丙景点'])
        self.assertNotIn('丙景点',visit_sequence(r[0]))
        self.assertTrue(r[0]['steps'][2]['excluded_unrealized_poi_mentions'])

    def test_joint_segment_not_distributed_or_mistaken_for_branch(self):
        r,_=extract(['【下午】','甲景点→乙景点→丙景点(15:40-17:00步行+乘车)','丙景点→丁景点(17:00-17:35乘车)'])
        self.assertEqual(len(r[0]['steps']),2);self.assertEqual(r[0]['steps'][0]['time']['end_minute'],1020)
        self.assertEqual(r[0]['steps'][0]['movement']['duration_scope'],'whole_segment')
        self.assertEqual(compare(r[0],r[0])['common_order_similarity'],1)

    def test_node_clocks_stay_local(self):
        r,_=extract(['1⃣甲景点(14:30)→2⃣乙景点→3⃣丙景点(18:30)'])
        self.assertEqual(len(r[0]['steps']),3)
        self.assertEqual([s['time']['period'] for s in r[0]['steps']],['下午','未说明','晚上'])

    def test_hyphen_before_5d(self):
        r,_=extract(['甲景点-5D城堡电影-乙景点'],words=('甲景点','乙景点','5D城堡电影'))
        self.assertEqual(len(r[0]['steps']),3)

    def test_time_and_day_do_not_cross_image_boundaries(self):
        d,e=fixture(['【Day 2】','【上午】','甲景点→乙景点→丙景点','甲景点→丙景点→丁景点'])
        src={(d['doc_id'],p['i']):{'kind':'image','source':'a' if p['i']<4 else 'b'} for p in d['paragraphs']}
        r,_=discover_all([d],{d['doc_id']:e},src)
        self.assertEqual(len(r),2)
        self.assertEqual([x['steps'][0]['time']['day'] for x in r],[2,None])
        self.assertTrue(all(s['time']['period']=='未说明' for s in r[1]['steps']))

    def test_numbered_detail_suppresses_arrow_overview(self):
        r,s=extract(['甲景点→乙景点→丙景点','第一站:甲景点','第二站:乙景点','第三站:丙景点'])
        self.assertEqual(len(r),1);self.assertEqual(r[0]['format'],'numbered')
        self.assertTrue(any(c['status']=='suppressed_duplicate' for c in s[0]['candidates']))

    def test_unrealized_narrative_not_visited(self):
        r,s=extract(['原计划先去甲景点,但没去','随后去乙景点','接着去丙景点'])
        self.assertEqual(visit_sequence(r[0]),['乙景点','丙景点'])
        self.assertTrue(any(c['reason']=='conditional_or_unrealized_action' for c in s[0]['candidates']))

    def test_connective_required_for_narrative(self):
        r,_=extract(['清晨第一站,便是甲景点','顺着栈道下行,抵达乙景点','继续前行遇见丙景点','丁景点非常美丽'])
        self.assertEqual(visit_sequence(r[0]),['甲景点','乙景点','丙景点'])

    def test_advisory_edge_rejected(self):
        r,s=extract(['甲景点→乙景点不建议徒步!'])
        self.assertFalse(r);self.assertEqual(s[0]['candidates'][0]['reason'],'advisory_edge_not_whole_route')

    def test_numbered_poi_list_without_route_context_rejected(self):
        r,s=extract(['1、甲景点很好玩','2、乙景点值得打卡'])
        self.assertFalse(r)
        self.assertTrue(all(c['reason']=='numbered_list_without_route_context' for c in s[0]['candidates']))

    def test_ocr_map_numbers_not_visit_order(self):
        r,_=extract(['游玩路线:','1.甲景点','7.乙景点','3.丙景点'])
        self.assertFalse(r)

    def test_explicit_list_after_clock_heading(self):
        r,_=extract(['09:00入园,以下依次游玩路线:','甲景点','未知活动','乙景点','12:00表演','丙景点'])
        self.assertEqual(len(r[0]['steps']),5);self.assertEqual(r[0]['steps'][3]['time']['period'],'中午')

    def test_tips_stop_list(self):
        r,_=extract(['以下依次游玩路线:','甲景点','乙景点','带娃划重点','丙景点记得备雨衣'])
        self.assertEqual(visit_sequence(r[0]),['甲景点','乙景点'])

    def test_reference_time_line_resets_old_period(self):
        r,_=extract(['下午|左线','时间线参考','08:00 甲景点→09:00 乙景点→10:00 丙景点'],kind='travel_note')
        self.assertTrue(all(s['time']['period']=='上午' for s in r[0]['steps']))
        self.assertEqual(r[0]['nature'],'visitor_recommendation')

    def test_truncated_ocr_rejected(self):
        d,e=fixture(['甲景点→乙景点→丙景点(断行'])
        r,s=discover_all([d],{d['doc_id']:e},{(d['doc_id'],1):{'kind':'image','source':'x'}})
        self.assertFalse(r);self.assertEqual(s[0]['candidates'][0]['reason'],'truncated_ocr_chain')

    def test_pairing_arbitrary_ids_and_metrics(self):
        a,ae=fixture(['甲景点→乙景点→丙景点'],doc_id='new-official')
        b,be=fixture(['乙景点→甲景点→丁景点'],doc_id='new-visitor',kind='travel_note')
        r,_=discover_all([a,b],{a['doc_id']:ae,b['doc_id']:be},{})
        c=automatic_pairs(r)[0]
        self.assertAlmostEqual(c['official_coverage'],2/3);self.assertAlmostEqual(c['jaccard'],.5)
        self.assertAlmostEqual(c['common_order_similarity'],.5);self.assertEqual(c['visitor_nature'],'visitor_unspecified')

    def test_single_common_poi_not_paired(self):
        a,ae=fixture(['甲景点→乙景点'],doc_id='a')
        b,be=fixture(['甲景点→丙景点'],doc_id='b',kind='travel_note')
        r,_=discover_all([a,b],{'a':ae,'b':be},{})
        self.assertFalse(automatic_pairs(r));self.assertIsNone(compare(r[0],r[1])['common_order_similarity'])


class FullPipeline(unittest.TestCase):
    def test_new_inputs_reproducible_without_config(self):
        (ROOT/'.test_tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'.test_tmp') as tmp:
            folder=Path(tmp);docs=[];edocs=[]
            for did,kind,line in [('uuid1','official_route','甲景点→乙景点→丙景点'),('uuid2','travel_note','乙景点→甲景点→丁景点')]:
                d,e=fixture([line],doc_id=did,kind=kind);docs.append(d);edocs.append(e)
            inputs={k:folder/(k+'.json') for k in ('p1','p2','sources')}
            inputs['p1'].write_text(json.dumps(docs,ensure_ascii=False),encoding='utf-8')
            inputs['sources'].write_text('[]',encoding='utf-8')
            inputs['p2'].write_text(json.dumps(dict(config_version='v2',input_sha256=digest(inputs['p1']),origin_sha256=digest(inputs['sources']),documents=edocs)),encoding='utf-8')
            original={k:digest(p) for k,p in inputs.items()};out=folder/'results';display=folder/'display'
            with patch.object(pipeline,'INPUTS',inputs),patch.object(pipeline,'OUT',out),patch.object(pipeline,'DISPLAY',display):
                pipeline.run(plots=False);first={p.name:digest(p) for p in out.glob('*')}
                pipeline.run(plots=False);self.assertEqual(first,{p.name:digest(p) for p in out.glob('*')})
            self.assertEqual(original,{k:digest(p) for k,p in inputs.items()})
            summary=json.loads((out/'summary.json').read_text('utf-8'))
            self.assertEqual(summary['routes'],2);self.assertEqual(summary['comparisons'],1)
            self.assertFalse((ROOT/'src/P3路线层级挖掘/config/routes.json').exists())

    def test_all_corpus_evidence_and_source_conflicts(self):
        docs=json.loads(pipeline.INPUTS['p1'].read_text('utf-8'))
        edocs={d['doc_id']:d for d in json.loads(pipeline.INPUTS['p2'].read_text('utf-8'))['documents']}
        src={(s['doc_id'],s['para_id']):s for s in json.loads(pipeline.INPUTS['sources'].read_text('utf-8'))}
        r,s=discover_all(docs,edocs,src)
        self.assertEqual(len(s),len(docs));self.assertEqual(validate(r,docs,edocs)['status'],'passed')
        self.assertTrue(any(n['kind']=='time_overlap_in_source' for n in diagnostics(r)))
        self.assertTrue(all(x['review_status']=='automatic_unreviewed' for x in r))


if __name__=='__main__':unittest.main()
