import copy
import json
from decimal import Decimal
from unittest.mock import patch
import httpx
from django.test import SimpleTestCase, override_settings
from .product_ai import (ProductAIError, public_documents, validate_plan,
    validate_assessments, select_products_ai, structured_response, PLAN_SCHEMA,api_error_message,extract_grounded_plan,materialize_line_quotes)


@override_settings(OPENAI_API_KEY='test-secret', OPENAI_MODEL='test-model', OPENAI_CHAT_ONLY=False)
class ProductAITests(SimpleTestCase):
    def test_device_does_not_match_compatible_consumables(self):
        from .product_ai import candidate_type_mismatch
        ink={'name':'Чернила Epson C13T03V44A, Yellow','category_label':'Периферия / Расходные материалы для принтеров и МФУ / Чернила для принтеров'}
        self.assertTrue(candidate_type_mismatch('Устройство многофункциональное',ink))
        self.assertTrue(candidate_type_mismatch('МФУ Epson L3360',ink))
        self.assertFalse(candidate_type_mismatch('Чернила для МФУ Epson',ink))
        self.assertFalse(candidate_type_mismatch('МФУ Epson L3360',{'name':'МФУ Epson L3360','category_label':'Периферия / МФУ'}))
        self.assertFalse(candidate_type_mismatch('Монитор',{'name':'Монитор AOC'}))

    @override_settings(OPENAI_CHAT_ONLY=True)
    def test_chat_policy_blocks_http_without_explicit_chat_scope(self):
        from .product_ai import authorized_chat_analysis, configured
        with patch('tender_search.core.product_ai.httpx.Client') as client:
            self.assertFalse(configured())
            with self.assertRaisesMessage(ProductAIError,'через чат'):
                structured_response('test',PLAN_SCHEMA,'test',{})
            client.assert_not_called()
            with authorized_chat_analysis():
                self.assertTrue(configured())
            self.assertFalse(configured())

    def setUp(self):
        taxonomy=patch('tender_search.core.product_ai.load_categories',return_value=[])
        taxonomy.start();self.addCleanup(taxonomy.stop)
        self.docs=public_documents([{'name':'spec.pdf','text':'Монитор с матрицей IPS. Доставка в офис.'}])
        self.plan={'summary':'Монитор IPS','single_product':True,'complete':True,
            'queries':['монитор IPS'],'requirements':[
                {'id':'R1','label':'Матрица IPS','quote':'матрицей IPS','document_id':'D1','scope':'product','ambiguous':False},
                {'id':'R2','label':'Доставка','quote':'Доставка в офис.','document_id':'D1','scope':'delivery','ambiguous':False}]}
        self.product={'name':'Монитор','url':'https://over-shop.kz/catalog/monitory/123/',
            'prices':{'online':'116','club':'104.40'},'stock':10,'stock_text':'10',
            'attributes':{'Матрица':'IPS'},'candidate_id':'P1'}
        self.assessment={'candidates':[{'candidate_id':'P1','verdicts':[
            {'requirement_id':'R1','state':'pass','attribute':'Матрица','value':'IPS','reason':'Совпадает'},
            {'requirement_id':'R2','state':'pass','attribute':'__name__','value':'Монитор','reason':'Не доказано'}]}]}
        self.ai_assessment=copy.deepcopy(self.assessment)
        self.ai_assessment['candidates'][0]['verdicts']=self.ai_assessment['candidates'][0]['verdicts'][:1]
        self.budget={'quantity':Decimal(3),'unit':Decimal(100),'total':Decimal(300)}

    def test_quotes_must_exist_in_the_named_document(self):
        validate_plan(self.plan,self.docs)
        self.plan['requirements'][0]['quote']='придуманная цитата'
        with self.assertRaises(ProductAIError):validate_plan(self.plan,self.docs)

    def test_citations_are_copied_from_exact_pdf_lines_without_retyping(self):
        docs=public_documents([{'text':'Описание\nфирме-\n производителе.\nСледующее условие'}])
        self.plan['requirements']=self.plan['requirements'][:1]
        req=self.plan['requirements'][0]
        req.pop('quote')
        req.update(start_line=2,end_line=3)
        materialize_line_quotes(self.plan,docs)
        self.assertEqual(req['quote'],'фирме-\n производителе.')
        validate_plan(self.plan,docs)
        req['end_line']=100
        with self.assertRaises(ProductAIError):materialize_line_quotes(self.plan,docs)

    def test_pdf_quote_retry_remains_exact_and_does_not_remove_requirements(self):
        invalid = copy.deepcopy(self.plan)
        invalid['requirements'][0]['quote'] = 'матрица IPS'
        repaired={'quotes':[{'id':'R1','quote':'матрицей IPS','document_id':'D1'}]}
        with patch('tender_search.core.product_ai.structured_response', side_effect=[invalid, repaired]) as ai:
            result = extract_grounded_plan('Монитор', self.docs)
        self.assertEqual(len(result['requirements']), 2)
        self.assertEqual(ai.call_count, 2)
        self.assertEqual([req['id'] for req in ai.call_args.args[3]['invalid_requirements']], ['R1'])

    def test_second_invalid_quote_stops_after_one_retry(self):
        self.plan['requirements'][0]['quote'] = 'выдуманная характеристика'
        repaired={'quotes':[{'id':'R1','quote':'выдуманная характеристика','document_id':'D1'}]}
        with patch('tender_search.core.product_ai.structured_response', side_effect=[self.plan,repaired]) as ai:
            with self.assertRaises(ProductAIError):
                extract_grounded_plan('Монитор', self.docs)
        self.assertEqual(ai.call_count, 2)

    def test_quote_repair_cannot_drop_an_unconfirmed_requirement(self):
        self.plan['requirements'][0]['quote']='пересказ IPS'
        self.plan['requirements'][1]['quote']='доставить куда-то'
        with patch('tender_search.core.product_ai.structured_response', side_effect=[self.plan, {'quotes':[{'id':'R1','quote':'матрицей IPS','document_id':'D1'}]}]):
            with self.assertRaises(ProductAIError):extract_grounded_plan('Монитор',self.docs)

    def test_invalid_schema_is_not_retried_as_a_quote_error(self):
        with patch('tender_search.core.product_ai.structured_response', return_value={}) as ai:
            with self.assertRaises(ProductAIError):
                extract_grounded_plan('Монитор', self.docs)
        self.assertEqual(ai.call_count, 1)

    def test_quote_typography_is_normalized_but_changed_numbers_are_rejected(self):
        docs=public_documents([{'text':'Диагональ более 23", но не более 30"'}])
        self.plan['requirements']=self.plan['requirements'][:1]
        self.plan['requirements'][0]['quote']='Диагональ более 23”, но не более 30”'
        validate_plan(self.plan,docs)
        self.plan['requirements'][0]['quote']='Диагональ более 24”, но не более 30”'
        with self.assertRaises(ProductAIError):validate_plan(self.plan,docs)

    def test_untrusted_or_ambiguous_evidence_never_confirms_requirement(self):
        result=validate_assessments(self.assessment,self.plan,[self.product])['P1']
        self.assertEqual([c['state'] for c in result['checks']],['pass','unknown'])
        self.assessment['candidates'][0]['verdicts'][0]['value']='VA'
        self.assertEqual(validate_assessments(self.assessment,self.plan,[self.product])['P1']['checks'][0]['state'],'unknown')
        self.assessment['candidates'][0]['verdicts'][0]['value']='IPS'
        self.plan['requirements'][0]['ambiguous']=True
        self.assertEqual(validate_assessments(self.assessment,self.plan,[self.product])['P1']['checks'][0]['state'],'unknown')

    def test_missing_duplicate_and_unhashable_ids_are_rejected(self):
        for rid in ['R9',[], 'R2']:
            value=copy.deepcopy(self.assessment);value['candidates'][0]['verdicts'][0]['requirement_id']=rid
            with self.assertRaises(ProductAIError):validate_assessments(value,self.plan,[self.product])
        self.assessment['candidates'][0]['verdicts'].pop()
        with self.assertRaises(ProductAIError):validate_assessments(self.assessment,self.plan,[self.product])

    def test_partial_and_oversize_documents_cannot_autofill(self):
        for docs in [[{'text':'IPS','truncated':True}],[{'text':'x'*100001}],[]]:
            with self.assertRaises(ProductAIError):public_documents(docs)

    def test_prices_and_stock_refresh_but_unchanged_technical_comparison_is_reused(self):
        docs=[{'name':'spec.pdf','text':self.docs[0]['text']}]
        with patch('tender_search.core.product_ai.search_catalogue',side_effect=lambda q:([copy.deepcopy(self.product)],1)),patch('tender_search.core.product_ai.structured_response',side_effect=[self.plan,self.ai_assessment]) as ai:
            result=select_products_ai('Монитор',docs,self.budget,Decimal(16))
            self.assertTrue(result['candidates'][0]['eligible'])
            self.assertEqual(result['candidates'][0]['price']['kind'],'online')
            self.assertEqual(result['candidates'][0]['unverified'],['Доставка'])
            select_products_ai('Монитор',docs,self.budget,Decimal(16),previous=result)
            self.assertEqual(ai.call_count,2)
            comparison=ai.call_args.args[-1]
            self.assertNotIn('prices',comparison['candidates'][0])
            self.assertNotIn('company_bin',comparison)
        self.product['stock']=1
        with patch('tender_search.core.product_ai.search_catalogue',return_value=([self.product],1)),patch('tender_search.core.product_ai.structured_response',return_value=self.plan) as ai:
            result=select_products_ai('Монитор',docs,self.budget,Decimal(16))
            self.assertFalse(result['candidates'][0]['eligible']);self.assertEqual(ai.call_count,1)

    def test_unknown_product_or_incomplete_plan_blocks_prefill(self):
        self.plan['complete']=False
        with patch('tender_search.core.product_ai.search_catalogue',return_value=([self.product],1)),patch('tender_search.core.product_ai.structured_response',side_effect=[self.plan,self.ai_assessment]):
            self.assertFalse(select_products_ai('Монитор',[{'text':self.docs[0]['text']}],self.budget,Decimal(16))['candidates'][0]['eligible'])

    def test_compared_candidates_remain_visible_when_all_are_rejected(self):
        products=[copy.deepcopy(self.product) for _ in range(7)]
        self.plan['complete']=False
        def response(name,schema,instructions,data):
            if name=='procurement_requirements':return self.plan
            candidates=[]
            for product in data['candidates']:
                candidate=copy.deepcopy(self.ai_assessment['candidates'][0]);candidate['candidate_id']=product['candidate_id']
                candidates.append(candidate)
            return {'candidates':candidates}
        with patch('tender_search.core.product_ai.search_catalogue',return_value=(products,7)),patch('tender_search.core.product_ai.structured_response',side_effect=response):
            result=select_products_ai('Монитор',[{'text':self.docs[0]['text']}],self.budget,Decimal(16))
        self.assertEqual(len(result['candidates']),6)
        self.assertTrue(all(p['checks'] for p in result['candidates']))
        self.assertEqual(result['compared'],7)

    def test_multiple_evidence_fields_require_every_pair_to_be_real(self):
        self.product['attributes'].update({'VGA':'есть','HDMI':'есть'})
        verdict=self.assessment['candidates'][0]['verdicts'][0]
        verdict.update(evidence=[{'attribute':'VGA','value':'есть'},{'attribute':'HDMI','value':'есть'}])
        checks=validate_assessments(self.assessment,self.plan,[self.product])['P1']['checks']
        self.assertEqual(checks[0]['state'],'pass');self.assertIn('HDMI',checks[0]['actual'])
        verdict['evidence'][1]['value']='нет'
        self.assertEqual(validate_assessments(self.assessment,self.plan,[self.product])['P1']['checks'][0]['state'],'unknown')

    def test_large_specification_splits_comparison_without_dropping_requirements(self):
        self.plan['requirements']=[{**self.plan['requirements'][0],'id':f'R{i}'} for i in range(1,41)]
        products=[copy.deepcopy(self.product) for _ in range(7)]
        sizes=[]
        def response(name,schema,instructions,data):
            self.assertEqual(len(data['requirements']),40)
            sizes.append(len(data['candidates']))
            return {'candidates':[{'candidate_id':p['candidate_id'],'verdicts':[
                {'requirement_id':r['id'],'state':'unknown','evidence':[],'reason':'Нет данных'}
                for r in data['requirements']]} for p in data['candidates']]}
        with patch('tender_search.core.product_ai.extract_grounded_plan',return_value=self.plan),patch('tender_search.core.product_ai.search_catalogue',return_value=(products,7)),patch('tender_search.core.product_ai.structured_response',side_effect=response):
            result=select_products_ai('Монитор',self.docs,self.budget,Decimal(16))
        self.assertEqual(sorted(sizes),[3,4])
        self.assertEqual(result['compared'],7)
        self.assertTrue(all(not p['eligible'] for p in result['candidates']))

    def test_changed_characteristics_invalidate_cache_and_obligations_are_not_sent(self):
        def catalogue(_):return [copy.deepcopy(self.product)],1
        with patch('tender_search.core.product_ai.search_catalogue',side_effect=catalogue),patch('tender_search.core.product_ai.structured_response',side_effect=[self.plan,self.ai_assessment,self.ai_assessment]) as ai:
            first=select_products_ai('Монитор',self.docs,self.budget,Decimal(16))
            self.product['attributes']['Матрица']='VA'
            second=select_products_ai('Монитор',self.docs,self.budget,Decimal(16),previous=first)
        self.assertEqual(ai.call_count,3)
        self.assertEqual(second['reused_comparisons'],0)
        self.assertFalse(second['candidates'][0]['eligible'])
        self.assertTrue(all(r['scope']=='product' for r in ai.call_args.args[-1]['requirements']))
        self.assertIn('Доставка',second['candidates'][0]['unverified'])

    def test_cached_comparison_still_rechecks_fresh_price_and_quantity(self):
        def catalogue(_):return [copy.deepcopy(self.product)],1
        with patch('tender_search.core.product_ai.search_catalogue',side_effect=catalogue),patch('tender_search.core.product_ai.structured_response',side_effect=[self.plan,self.ai_assessment]) as ai:
            first=select_products_ai('Монитор',self.docs,self.budget,Decimal(16))
            self.product['prices']={'online':'100','club':'90'}
            second=select_products_ai('Монитор',self.docs,self.budget,Decimal(16),previous=first)
            self.assertEqual(second['reused_comparisons'],1)
            self.assertEqual(Decimal(second['candidates'][0]['price']['unit_gross']),Decimal('100.00'))
            self.product['stock']=1
            third=select_products_ai('Монитор',self.docs,self.budget,Decimal(16),previous=second)
            self.assertFalse(third['candidates'][0]['eligible'])
            self.assertEqual(ai.call_count,2)

    def test_incomplete_cache_cannot_skip_missing_requirement(self):
        def catalogue(_):return [copy.deepcopy(self.product)],1
        with patch('tender_search.core.product_ai.search_catalogue',side_effect=catalogue),patch('tender_search.core.product_ai.structured_response',side_effect=[self.plan,self.ai_assessment,self.ai_assessment]) as ai:
            first=select_products_ai('Монитор',self.docs,self.budget,Decimal(16))
            for key in first['comparison_cache']:first['comparison_cache'][key]=[]
            second=select_products_ai('Монитор',self.docs,self.budget,Decimal(16),previous=first)
        self.assertEqual(ai.call_count,3)
        self.assertEqual(second['reused_comparisons'],0)

    def test_metadata_is_checked_by_code_and_not_sent_to_product_comparison(self):
        docs=[{'text':'Монитор с матрицей IPS. Доставка в офис. Количество: 3 Единица измерения: Штука'}]
        self.plan['requirements'].extend([
            {'id':'R3','label':'Количество','quote':'Количество: 3','document_id':'D1','scope':'product','ambiguous':False},
            {'id':'R4','label':'Единица','quote':'Единица измерения: Штука','document_id':'D1','scope':'unit','ambiguous':False}])
        self.budget['measure']='шт.'
        with patch('tender_search.core.product_ai.search_catalogue',return_value=([self.product],1)),patch('tender_search.core.product_ai.structured_response',side_effect=[self.plan,self.ai_assessment]) as ai:
            result=select_products_ai('Монитор',docs,self.budget,Decimal(16))
        self.assertEqual(len(ai.call_args.args[-1]['requirements']),1)
        self.assertTrue(result['candidates'][0]['eligible'])
        self.assertEqual([c['state'] for c in result['candidates'][0]['checks'][-2:]],['pass','pass'])

    def test_expensive_matching_candidate_is_compared_and_preferred_over_cheap_mismatch(self):
        products=[]
        for n in range(8):
            p=copy.deepcopy(self.product);p['name']=f'Product {n}';p['prices']={'online':str(50+n)}
            products.append(p)
        def response(name,schema,instructions,data):
            if name=='procurement_requirements':return self.plan
            candidates=[]
            for product in data['candidates']:
                candidate=copy.deepcopy(self.ai_assessment['candidates'][0]);candidate['candidate_id']=product['candidate_id']
                if product['name']!='Product 7':candidate['verdicts'][0]['state']='fail'
                candidates.append(candidate)
            return {'candidates':candidates}
        with patch('tender_search.core.product_ai.search_catalogue',return_value=(products,8)),patch('tender_search.core.product_ai.structured_response',side_effect=response):
            result=select_products_ai('Монитор',[{'text':self.docs[0]['text']}],self.budget,Decimal(16))
        self.assertEqual(result['compared'],8)
        self.assertEqual(result['candidates'][0]['name'],'Product 7')
        self.assertTrue(result['candidates'][0]['eligible'])

    def test_quantity_disagreement_blocks_prefill_and_product_cannot_be_metadata(self):
        docs=[{'text':self.docs[0]['text']+' Количество: 4'}]
        self.plan['requirements'].append({'id':'R3','label':'Количество','quote':'Количество: 4','document_id':'D1','scope':'quantity','ambiguous':False})
        with patch('tender_search.core.product_ai.search_catalogue',return_value=([self.product],1)),patch('tender_search.core.product_ai.structured_response',side_effect=[self.plan,self.ai_assessment]):
            result=select_products_ai('Монитор',docs,self.budget,Decimal(16))
        self.assertFalse(result['candidates'][0]['eligible'])
        self.assertEqual(result['candidates'][0]['checks'][-1]['state'],'fail')
        self.plan['requirements'][0]['scope']='unit'
        with self.assertRaises(ProductAIError):validate_plan(self.plan,public_documents(docs))

    def test_responses_request_is_structured_and_errors_do_not_expose_provider_body(self):
        actual_client=httpx.Client
        def handler(request):
            payload=json.loads(request.content)
            self.assertFalse(payload['store']);self.assertTrue(payload['text']['format']['strict'])
            return httpx.Response(200,json={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(self.plan)}]}]})
        with patch('tender_search.core.product_ai.httpx.Client',side_effect=lambda **kw:actual_client(transport=httpx.MockTransport(handler))):
            self.assertEqual(structured_response('test',PLAN_SCHEMA,'extract',{}),self.plan)
        for code in [401,429,500]:
            with patch('tender_search.core.product_ai.httpx.Client',side_effect=lambda **kw:actual_client(transport=httpx.MockTransport(lambda r:httpx.Response(code,text='test-secret provider body')))):
                with self.assertRaises(ProductAIError) as error:structured_response('test',PLAN_SCHEMA,'extract',{})
                self.assertNotIn('test-secret',str(error.exception));self.assertNotIn('provider body',str(error.exception))

    @override_settings(OPENAI_API_KEY='')
    def test_no_key_never_calls_network(self):
        with patch('tender_search.core.product_ai.httpx.Client') as network:
            with self.assertRaises(ProductAIError):structured_response('test',PLAN_SCHEMA,'extract',{})
            network.assert_not_called()

    def test_timeout_is_distinguished_from_invalid_json_without_private_details(self):
        actual_client=httpx.Client
        def timeout(request):
            raise httpx.ReadTimeout('test-secret',request=request)
        for handler, expected in [(timeout,'не ответил вовремя'),
                (lambda request:httpx.Response(200,text='test-secret'),'корректный ответ')]:
            with patch('tender_search.core.product_ai.httpx.Client',side_effect=lambda **kw:actual_client(transport=httpx.MockTransport(handler))):
                with self.assertRaisesMessage(ProductAIError,expected) as caught:
                    structured_response('procurement_comparison',PLAN_SCHEMA,'test',{})
                self.assertNotIn('test-secret',str(caught.exception))

    def test_temporary_provider_failure_retries_once_but_access_failure_does_not(self):
        actual_client=httpx.Client
        responses=[httpx.Response(520,text='test-secret'),httpx.Response(200,json={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'{}'}]}]})]
        calls=[]
        def handler(request):
            calls.append(request)
            return responses.pop(0)
        with patch('tender_search.core.product_ai.httpx.Client',side_effect=lambda **kw:actual_client(transport=httpx.MockTransport(handler))):
            self.assertEqual(structured_response('test',PLAN_SCHEMA,'test',{}),{})
        self.assertEqual(len(calls),2)
        with patch('tender_search.core.product_ai._structured_response_once',side_effect=ProductAIError('Нет доступа')) as once:
            with self.assertRaises(ProductAIError):structured_response('test',PLAN_SCHEMA,'test',{})
            self.assertEqual(once.call_count,1)

    def test_validated_requirements_are_checkpointed_before_catalogue_failure(self):
        checkpoints=[]
        with patch('tender_search.core.product_ai.extract_grounded_plan',return_value=self.plan) as extract, patch('tender_search.core.product_ai.search_catalogue',side_effect=ValueError('catalogue unavailable')):
            with self.assertRaisesMessage(ValueError,'catalogue unavailable'):
                select_products_ai('Монитор',self.docs,self.budget,Decimal(16),progress=lambda stage,values:checkpoints.append((stage,values)))
            saved={k:v for stage,values in checkpoints for k,v in values.items()}
            self.assertEqual(checkpoints[-1][0],'catalogue')
            self.assertEqual(saved['requirements_plan'],self.plan)
            with self.assertRaises(ValueError):
                select_products_ai('Монитор',self.docs,self.budget,Decimal(16),previous=saved)
            self.assertEqual(extract.call_count,1)

    def test_actual_project_model_denial_has_actionable_message_without_raw_details(self):
        raw=json.dumps({'error':{'code':'model_not_found','message':'Project private-project does not have access to model test-secret'}})
        message=api_error_message(403,raw)
        self.assertIn('OPENAI_MODEL',message)
        self.assertNotIn('private-project',message);self.assertNotIn('test-secret',message)
        raw=json.dumps({'error':'Missing scopes: api.model.read. test-secret'})
        self.assertIn('прав ключа',api_error_message(403,raw))
        self.assertNotIn('test-secret',api_error_message(403,raw))
        self.assertIn('регион',api_error_message(403,json.dumps({'error':{'code':'unsupported_country_region_territory'}})))
        self.assertIn('ключ',api_error_message(401,b'not-json test-secret'))

    def test_streaming_403_is_classified_as_model_access_in_the_real_client_path(self):
        actual_client=httpx.Client
        transport=httpx.MockTransport(lambda request:httpx.Response(403,json={'error':{'code':'model_not_found','message':'test-secret'}}))
        with patch('tender_search.core.product_ai.httpx.Client',side_effect=lambda **kw:actual_client(transport=transport)):
            with self.assertRaisesMessage(ProductAIError,'Проект OpenAI не имеет доступа'):
                structured_response('test',PLAN_SCHEMA,'extract',{})
