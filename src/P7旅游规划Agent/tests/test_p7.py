import json
import os
import unittest
from uuid import uuid4


from p7 import agent, conversation as chat, itinerary, knowledge, models
from p7.api import cancel
from p7.db import engine
from p7.errors import BusinessError
from p7.usage import SUMMARY_REQUEST, loop_control, parse_summary, provider_usage

POI='POI-c9eafeca46dfede7'
ROUTE='GZCL-NOTE-12-R-f5fee04dec'
STEP=ROUTE+'-S002'
SUMMARY={'requirements':['安排一天'],'evidence':[],'itinerary':[],'open_questions':['待确认景区']}


def action(name,args,call_id='call_1',tokens=1200):
    return {'usage':{'prompt_tokens':tokens,'total_tokens':tokens+10},'choices':[{'message':{
        'tool_calls':[{'id':call_id,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]
    }}]}


class Usage(unittest.TestCase):
    def test_loop_control_thresholds(self):
        self.assertEqual(loop_control(7,19),{})
        self.assertEqual(loop_control(8,19),{'loop_control':{'remaining_decisions':2,'remaining_tools':11}})
        self.assertEqual(loop_control(7,20),{'loop_control':{'remaining_decisions':3,'remaining_tools':10}})

    def test_actual_prompt_usage_and_summary_validation(self):
        self.assertEqual(provider_usage({'usage':{'prompt_tokens':100_001,'total_tokens':100_500}}),100_001)
        self.assertIsNone(provider_usage({'usage':{}}))
        self.assertEqual(parse_summary({'choices':[{'message':{'content':json.dumps(SUMMARY)}}]}),SUMMARY)
        with self.assertRaises(BusinessError) as invalid:
            parse_summary({'choices':[{'message':{'content':'{}'}}]})
        self.assertEqual(invalid.exception.code,'CONTEXT_SUMMARY_FAILED')


@unittest.skipUnless(os.environ.get('P7_1_RUN_DB_TESTS'),'set P7_1_RUN_DB_TESTS=1 for PostgreSQL integration')
class Integration(unittest.TestCase):
    def test_knowledge_and_versioned_itinerary_transaction(self):
        with engine().connect() as conn:
            tx=conn.begin()
            try:
                conv=chat.create(conn,'P7_1 事务测试')
                version=conv['data_version']
                cid=conv['id']
                found=knowledge.route_details(conn,version,models.RouteDetails(route_id=ROUTE,step_ids=[STEP]))
                self.assertIn(POI,{p['poi_id'] for p in found['steps'][0]['pois']})
                item={'type':'poi_visit','poi_id':POI,'route_id':ROUTE,'step_id':STEP,
                      'recommendation_reason':'与历史路线步骤对应','evidence_refs':[{'kind':'node','id':STEP}]}
                plan=itinerary.submit(conn,cid,models.SubmitItinerary(title='测试行程',days=[{'items':[item]}]),'create')
                iid=plan['itinerary_id']
                day=plan['days'][0]
                iid_item=day['items'][0]['item_id']
                edited=models.Edit(base_version=1,operations=[
                    {'op':'insert','day_id':day['day_id'],'anchor':{'after_item_id':iid_item},'item':item},
                    {'op':'update_fields','target':{'item_id':iid_item},'fields':{'optional':True}},
                ])
                second=itinerary.update(conn,cid,iid,edited,'edit')
                self.assertEqual(second['version'],2)
                self.assertEqual(itinerary.update(conn,cid,iid,edited,'edit')['version'],2)
                self.assertEqual(len(itinerary.view(conn,iid,1)['days'][0]['items']),1)
                self.assertEqual(len(second['days'][0]['items']),2)
                self.assertRaisesRegex(BusinessError,'行程已更新',itinerary.update,conn,cid,iid,edited,'stale')
                delete=models.Edit(base_version=2,operations=[{'op':'delete','target':{'route_id':ROUTE,'entity_id':POI}}])
                with self.assertRaises(BusinessError) as error:
                    itinerary.update(conn,cid,iid,delete,'ambiguous')
                self.assertEqual(error.exception.code,'AMBIGUOUS_TARGET')
                third=itinerary.update(conn,cid,iid,models.Edit(base_version=2,operations=[
                    {'op':'delete','target':{'item_id':iid_item}}
                ]),'delete')
                self.assertEqual(third['version'],3)
                self.assertEqual(len(third['days'][0]['items']),1)
            finally:
                tx.rollback()

    def test_api_react_clarification_compaction_and_resume(self):
        submitted={'title':'青龙山亲子行程','days':[{'items':[
           {'type':'poi_visit','poi_id':POI,'route_id':ROUTE,'step_id':STEP,'recommendation_reason':'与历史路线步骤对应'}
        ]}]}
        calls=[]
        responses=iter([action('ask_user',{'question':'想去哪个景区？','fields':['景区'],'options':['广州长隆']},tokens=100_000),
                        action('search_routes',{'scenic_codes':['GZCL'],'days':1},tokens=100_001),
                        {'choices':[{'message':{'content':json.dumps(SUMMARY)}}]},
                        action('submit_itinerary',submitted,tokens=800)])
        def mock_provider(messages,tools):
            self.assertEqual(tools,agent.SCHEMAS)
            calls.append(messages)
            return next(responses)
        with engine().begin() as conn:
            cid=chat.create(conn,'P7_1 回归：澄清与压缩')['id']
            first=chat.admit(conn,cid,models.NewTurn(client_message_id=uuid4(),text='帮我排一天'))
        agent.run_turn(first,mock_provider)
        with engine().begin() as conn:
            q=chat.public_conv(conn,cid)['pending_question']['id']
            request=models.NewTurn(client_message_id=uuid4(),text='广州长隆，安排一天。',reply_to=q)
            second=chat.admit(conn,cid,request)
            self.assertEqual(chat.admit(conn,cid,request),second)
            self.assertEqual(chat.get_turn(conn,second)['decision_count'],0)
        agent.run_turn(second,mock_provider)
        with engine().connect() as conn:
            self.assertEqual(chat.get_turn(conn,first)['status'],'awaiting_user')
            result=chat.get_turn(conn,second)
            self.assertEqual(result['status'],'completed')
            self.assertEqual((result['decision_count'],result['tool_call_count']),(2,2))
            stored=chat.get(conn,cid)
            self.assertEqual(stored['context_summary'],SUMMARY)
            self.assertEqual(stored['summary_through_seq'],4)
        self.assertEqual(calls[1][:len(calls[0])],calls[0])
        self.assertEqual(calls[2][:-1],calls[1][:-1])
        self.assertEqual(calls[2][-1],SUMMARY_REQUEST)
        self.assertEqual([m['role'] for m in calls[3]],['system','user','user','assistant','tool'])
        self.assertTrue(all(call[0]==calls[0][0] for call in calls))
        self.assertEqual(calls[3][2],calls[1][-1])
        self.assertEqual(calls[3][3]['tool_calls'],
                         action('search_routes',{'scenic_codes':['GZCL'],'days':1})['choices'][0]['message']['tool_calls'])
        self.assertEqual(calls[3][-1]['tool_call_id'],'call_1')
        self.assertEqual(json.loads(calls[3][-1]['content'])['status'],'ok')
        self.assertNotIn(SUMMARY_REQUEST,calls[3])

        with engine().connect() as conn:
            history=chat.message_list(conn,cid)
            self.assertEqual([m['role'] for m in history['items']],['user','assistant','user','assistant'])
            first_user, question, second_user, final=history['items']
            self.assertEqual(first_user['turn_id'],question['turn_id'])
            self.assertEqual(second_user['turn_id'],final['turn_id'])
            self.assertEqual(second_user['markdown'],'广州长隆，安排一天。')
            self.assertIn('想去哪个景区？',calls[1][-1]['content'])
            steps=[x for x in history['activities'] if x['turn_id']==second]
            self.assertEqual([x['event_type'] for x in steps],
                             ['tool.started','tool.progress','tool.started','tool.progress'])
            self.assertEqual([x['payload']['tool'] for x in steps],
                             ['search_routes','search_routes','submit_itinerary','submit_itinerary'])
            self.assertEqual(steps[0]['payload']['activity_id'],steps[1]['payload']['activity_id'])
            self.assertNotIn('arguments',steps[0]['payload'])
            last_only=chat.message_list(conn,cid,limit=1)
            self.assertEqual(last_only['items'][0]['turn_id'],second)
            self.assertEqual(last_only['activities'],steps)

    def test_model_messages_are_append_only_across_user_turns(self):
        with engine().begin() as conn:
            cid=chat.create(conn,'P7_1 回归：只追加上下文')['id']
            first=chat.admit(conn,cid,models.NewTurn(client_message_id=uuid4(),text='查一下广州长隆一天的路线。'))
        requests=[]

        def respond(messages,tools):
            requests.append(messages)
            decision=len(requests)
            if decision<=8:
                return action('search_pois',{'keywords':['青龙山']},f'call_{decision}',tokens=1500+decision)
            return {'choices':[{'message':{'content':'已查询。'}}], 'usage':{'prompt_tokens':1800}}

        agent.run_turn(first,respond)
        with engine().begin() as conn:
            second=chat.admit(conn,cid,models.NewTurn(client_message_id=uuid4(),text='查一下珠海长隆两天的路线。'))
        agent.run_turn(second,respond)
        self.assertEqual(len(requests),10)
        for previous,current in zip(requests,requests[1:]):
            self.assertEqual(previous,current[:len(previous)])
        self.assertNotIn('loop_control',json.loads(requests[7][-1]['content']))
        self.assertEqual(json.loads(requests[8][-1]['content'])['loop_control'],
                         {'remaining_decisions':2,'remaining_tools':22})
        self.assertIn('"scenic_codes":["GZCL"]',requests[0][-1]['content'])
        self.assertIn('"scenic_codes":["ZHCL"]',requests[-1][-1]['content'])
        self.assertNotIn('remaining_decisions',requests[-1][-1]['content'])

    def test_model_and_tool_caps(self):
        with engine().begin() as conn:
            cid=chat.create(conn,'P7_1 回归：调用预算')['id']
            tid=chat.admit(conn,cid,models.NewTurn(client_message_id=uuid4(),text='查询景区'))
        batches=iter([31]+[4]*7+[2,1])
        requests=[]
        def provider(messages,tools):
            self.assertEqual(tools,agent.SCHEMAS)
            requests.append(messages)
            count=next(batches)
            calls=[action('search_pois',{'keywords':['青龙山']},str(i))['choices'][0]['message']['tool_calls'][0] for i in range(count)]
            return {'choices':[{'message':{'tool_calls':calls}}]}
        agent.run_turn(tid,provider)
        with engine().connect() as conn:
            result=chat.get_turn(conn,tid)
        self.assertEqual((result['decision_count'],result['tool_call_count'],result['status']),(10,30,'partial'))
        for previous,current in zip(requests,requests[1:]):
            self.assertEqual(previous,current[:len(previous)])
        results=[json.loads(message['content']) for message in requests[-1] if message['role']=='tool']
        self.assertTrue(all('loop_control' not in r for r in results[:31+19]))
        self.assertEqual(results[31+19]['loop_control'],{'remaining_decisions':4,'remaining_tools':10})
        self.assertEqual(results[-1]['loop_control'],{'remaining_decisions':1,'remaining_tools':0})
        with engine().connect() as conn:
            conv=chat.get(conn,cid)
            final_context=agent.context_messages(conv,agent.context_history(conn,conv))
        self.assertEqual(json.loads(final_context[-2]['content'])['error']['code'],'TOOL_BUDGET_EXCEEDED')

    def test_cancel_before_tool_dispatch(self):
        with engine().begin() as conn:
            cid=chat.create(conn,'P7_1 回归：取消')['id']
            tid=chat.admit(conn,cid,models.NewTurn(client_message_id=uuid4(),text='帮我查路线'))
        def interrupted(messages,tools):
            self.assertEqual(cancel(tid).status_code,202)
            return action('search_routes',{})
        agent.run_turn(tid,interrupted)
        with engine().connect() as conn:
            run=chat.get_turn(conn,tid)
            self.assertEqual((run['status'],run['decision_count'],run['tool_call_count']),('cancelled',1,0))
            self.assertEqual(cancel(tid),{'status':'cancelled'})


if __name__=='__main__':
    unittest.main()
