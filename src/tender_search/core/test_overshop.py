from decimal import Decimal
from unittest.mock import patch
from bs4 import BeautifulSoup
import httpx
from django.test import SimpleTestCase,TestCase,override_settings,Client
from .overshop import parse_product,choose_price,compare,public_url,requirement_lines,required_product_model,select_products,catalogue_queries,search_catalogue
from .models import ProductAnalysis,Document,DocumentVersion,UserLotPrice,UserLotWorkflow


class CatalogueTests(SimpleTestCase):
    def test_verified_section_supplies_candidates_when_general_search_is_empty(self):
        empty=BeautifulSoup('<div class="catalog_block"></div>','html.parser')
        section=BeautifulSoup('<div class="catalog_block"><div class="item-title"><a href="/catalog/monitory/123/">Монитор</a></div></div>','html.parser')
        for first in (empty,httpx.ConnectError('search unavailable')):
            with patch('tender_search.core.overshop.fetch',side_effect=[first,section,section]) as network,patch('tender_search.core.overshop.parse_product',return_value={'name':'Монитор'}):
                products,scanned=search_catalogue(['монитор'],category_paths=['/catalog/monitory/'])
                self.assertEqual(scanned,1);self.assertEqual(products,[{'name':'Монитор'}])
                self.assertEqual(network.call_args_list[1].args[1],'https://over-shop.kz/catalog/monitory/')

    def test_one_failed_search_does_not_discard_other_real_results(self):
        listing=BeautifulSoup('<div class="catalog_block"><div class="item-title"><a href="/catalog/monitory/123/">Монитор</a></div></div>','html.parser')
        product={'name':'Монитор'}
        with patch('tender_search.core.overshop.fetch',side_effect=[httpx.ConnectError('unavailable'),listing,listing]),patch('tender_search.core.overshop.parse_product',return_value=product):
            self.assertEqual(search_catalogue(['failing query','monitor']),([product],1))
        with patch('tender_search.core.overshop.fetch',side_effect=httpx.ConnectError('unavailable')):
            with self.assertRaisesMessage(ValueError,'временно недоступен'):search_catalogue(['query'])

    def test_public_parser_uses_labelled_prices_attributes_and_stock(self):
        soup=BeautifulSoup('''<h1>Монитор IPS</h1>
          <div class="price_group"><span class="price_name">Онлайн цена</span><span class="price" data-currency="KZT" data-value="11600"></span></div>
          <div class="price_group"><span class="price_name">Клубная цена</span><span class="price" data-currency="KZT" data-value="10440"></span></div>
          <table class="props_list"><tr><td>Диагональ</td><td>24 дюйма</td></tr></table>
          <div class="item-stock">На складах : 31 в 1 магазине</div>''','html.parser')
        product=parse_product(soup,'https://over-shop.kz/catalog/monitory/123/')
        self.assertEqual(product['prices'],{'online':'11600','club':'10440'})
        self.assertEqual(product['attributes'],{'Диагональ':'24 дюйма'})
        self.assertEqual(product['stock'],31)
        self.assertIsNone(public_url('https://example.com/catalog/monitory/123/'))
        self.assertIsNone(public_url('/catalog/../auth/'))

    def test_online_first_club_fallback_and_both_unit_total_caps_with_and_without_vat(self):
        product={'prices':{'online':'116','club':'104.40'}}
        budget={'quantity':Decimal(3),'unit':Decimal(100),'total':Decimal(300)}
        self.assertEqual(choose_price(product,budget,Decimal(16))['kind'],'online')
        budget['unit']=Decimal(95);budget['total']=Decimal(285)
        self.assertEqual(choose_price(product,budget,Decimal(16))['kind'],'club')
        budget['total']=Decimal(269)
        self.assertIsNone(choose_price(product,budget,Decimal(16)))
        budget.update(unit=Decimal(110),total=Decimal(330))
        self.assertEqual(choose_price(product,budget,Decimal(0))['kind'],'club')
        budget['unit']=Decimal(100)
        self.assertIsNone(choose_price(product,budget,Decimal(0)))

    def test_uncertain_units_ranges_and_unsupported_requirements_are_not_confirmed(self):
        text='Диагональ не менее 24 дюймов\nЯркость не менее 250 кд\nSSD не менее 512 ГБ\nДиагональ от 20 до 25 дюймов\nПорты HDMI'
        checks,unknown=compare(text,{'Диагональ':'23.8 дюйма','Яркость':'300 кд','SSD':'1 ТБ'})
        self.assertEqual([c['state'] for c in checks],['fail','pass'])
        self.assertEqual(len(unknown),3)

    def test_panel_mismatch_and_resolution_are_not_ignored(self):
        checks,unknown=compare('Матрица IPS\nРазрешение 1920x1080',{'Тип матрицы':'VA','Разрешение':'1920x1080'})
        self.assertEqual([c['state'] for c in checks],['fail','pass'])
        self.assertFalse(unknown)

    def test_real_pdf_columns_and_wrapped_requirement_are_rejoined(self):
        text='Описание\tи\tтребуемые                 Монитор.\tФирма\tмодель\tXTREON\tXT2753DE.\tТип\nфункциональные,                      матрицы\tIPS.\nтехнические,\tкачественные\tи          Разрешение\tне\tменее\nэксплуатационные                     1920×1080\t(Full\tHD).\nхарактеристики\tзакупаемых            Время\tотклика\tне\tболее\nтоваров:                             5\tмс.'
        normalized=requirement_lines(text)
        self.assertIn('Фирма модель XTREON XT2753DE',normalized)
        self.assertIn('Тип матрицы IPS',normalized)
        self.assertIn('Время отклика не более 5 мс',normalized)
        self.assertNotIn('функциональные',normalized)
        self.assertEqual(required_product_model('Монитор 23,8 24Е1N1100A/01 IPS 1920x1080 120Hz 1мс'),'24E1N1100A/01')

    def test_no_understood_requirements_does_not_autofill_and_other_categories_are_discarded(self):
        listing=BeautifulSoup('<div class="catalog_block"><div class="item-title"><a href="/catalog/monitory/123/">Монитор</a></div></div>','html.parser')
        product={'name':'Монитор AOC','url':'https://over-shop.kz/catalog/monitory/123/',
                 'prices':{'online':'116'},'attributes':{},'stock':5,'stock_text':'5'}
        budget={'quantity':Decimal(1),'unit':Decimal(100),'total':Decimal(100)}
        with patch('tender_search.core.overshop.fetch',return_value=listing),patch('tender_search.core.overshop.parse_product',return_value=product):
            result=select_products('Монитор','Требование, которое пока нельзя разобрать',budget,Decimal(16))
            self.assertFalse(result['candidates'][0]['eligible'])
            product['name']='Термопрокладка'
            self.assertFalse(select_products('Монитор','Тип матрицы IPS',budget,Decimal(16))['candidates'])

    def test_real_compact_monitor_requirements_keep_comparator_direction(self):
        text='"Тип матрицы IPS Тип поверхности экрана Матовая Диагональ экрана, дюйм не менее 24 Соотношение сторон не менее 16:9 Максимальное разрешение не менее 1920 x 1080 Full HD Частота при максимальном разрешении, не менее 120 Гц Яркость, не менее кд/м2 250, время отклика пикселя, мс не менее 5, Контрастность не более 700:1 Динамическая контрастность не менее 20М:1'
        attrs={'Тип матрицы':'IPS','Диагональ, дюйм':'23.8','Соотношение сторон':'16:9',
               'Разрешение':'1920x1080','Частота обновления, Гц':'100','Яркость, кд/м²':'300',
               'Время отклика, мс':'1','Контрастность':'1000:1'}
        checks,unknown=compare(text,attrs)
        self.assertEqual([c['state'] for c in checks],['pass','fail','pass','pass','fail','pass','fail','fail'])
        self.assertTrue(any('Динамическая' in s for s in unknown))

    def test_exact_diagonal_is_not_a_minimum_and_resolution_checks_both_axes(self):
        checks,_=compare('Диагональ экрана 27". Разрешение не менее 1920×1080 (Full HD).',
                         {'Диагональ, дюйм':'23.8','Разрешение':'2560x720'})
        self.assertEqual([c['state'] for c in checks],['fail','fail'])

    def test_catalogue_query_uses_diagonal_but_does_not_guess_a_range_or_override_model(self):
        self.assertEqual(catalogue_queries('Монитор','Диагональ экрана, дюйм 27',''),['Монитор 27','Монитор'])
        self.assertEqual(catalogue_queries('Монитор','Диагональ от 20 до 25 дюймов',''),['Монитор'])
        self.assertEqual(catalogue_queries('Монитор','Диагональ 27 дюймов','AOC ABC'),['AOC ABC'])

    def test_real_catalogue_attribute_names_and_thousand_separators(self):
        checks,unknown=compare('Частота при максимальном разрешении, Гц 100\nКонтрастность не более 700:1\nСтандарт крепления VESA 100x100 мм\nЦвет, используемый в оформлении Черный\nИнтерфейс подключения VGA, HDMI',{
            'Макс. Частота обновления кадров, Гц':'100','Динамическая частота обновления экрана':'есть',
            'Контрастность':'1 300 :1','Стандарт настенного крепления':'100x100 мм',
            'Цвет корпуса':'черный','Вход VGA':'есть','Вход HDMI':'нет'})
        self.assertEqual([c['state'] for c in checks],['pass','fail','pass','pass','fail'])
        self.assertFalse(unknown)


@override_settings(OPENAI_API_KEY='test-only',OPENAI_MODEL='test-model',OPENAI_CHAT_ONLY=False,STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},
    'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ProductAnalysisTests(TestCase):
    @override_settings(OPENAI_CHAT_ONLY=True)
    def test_chat_policy_rejects_ui_launch_without_enqueuing(self):
        with patch('tender_search.core.tasks.analyze_preparation_product.delay') as task:
            response=self.client.post(self.endpoint)
        self.assertEqual(response.status_code,409)
        self.assertIn('через чат',response.json()['error'])
        task.assert_not_called()

    def setUp(self):
        from .test_preparation import PreparationTests
        PreparationTests.setUp(self)
        self.endpoint=self.url+'product-analysis/'
        doc=Document.objects.create(announcement=self.lot.announcement,lot=self.lot,source_id='spec',name='Техническая спецификация',kind='technical',url='https://zakup.gov.kz/spec')
        DocumentVersion.objects.create(document=doc,sha256='e'*64,text='Диагональ не менее 24 дюймов')

    @override_settings(OPENAI_API_KEY='')
    def test_no_key_does_not_dispatch_analysis(self):
        with patch('tender_search.core.tasks.analyze_preparation_product.delay') as dispatch:
            self.assertEqual(self.client.post(self.endpoint).status_code,409)
            dispatch.assert_not_called()
        self.assertFalse(ProductAnalysis.objects.exists())

    def test_previous_algorithm_result_is_not_reused_for_prefill(self):
        ProductAnalysis.objects.create(owner=self.owner,lot=self.lot,state='done',generation='old',result={'engine':'openai','candidates':[]})
        response=self.client.get(self.endpoint)
        self.assertEqual(response.json()['state'],'outdated')
        self.assertNotIn('result',response.json())

    def test_dispatch_deduplicates_pending_job_and_does_not_save_price_or_preparation(self):
        with patch('tender_search.core.tasks.analyze_preparation_product.delay') as dispatch:
            with self.captureOnCommitCallbacks(execute=True):response=self.client.post(self.endpoint)
            self.assertEqual(response.status_code,200)
            dispatch.assert_called_once()
            with self.captureOnCommitCallbacks(execute=True):self.client.post(self.endpoint)
            dispatch.assert_called_once()
        self.assertEqual(ProductAnalysis.objects.count(),1)
        self.assertFalse(UserLotPrice.objects.exists())
        self.assertEqual(UserLotWorkflow.objects.get(owner=self.owner,lot=self.lot).stage,'preparing')

    def test_owner_stage_and_csrf_gates(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.endpoint).json(),{'state':'none','ai_configured':True})
        UserLotWorkflow.objects.filter(owner=self.other,lot=self.lot).update(stage='applied')
        self.assertEqual(self.client.post(self.endpoint).status_code,404)
        strict=Client(enforce_csrf_checks=True);strict.force_login(self.owner)
        self.assertEqual(strict.post(self.endpoint).status_code,403)

    def test_task_reads_specification_and_uses_current_company_vat_without_touching_bid(self):
        from .tasks import analyze_preparation_product
        job=ProductAnalysis.objects.create(owner=self.owner,lot=self.lot,generation='sample')
        result={'candidates':[],'query':'monitor','limitation':'manual','scanned':0}
        with patch('tender_search.core.product_ai.select_products_ai',return_value=result) as select:
            analyze_preparation_product(job.pk,'old-generation')
            select.assert_not_called()
            analyze_preparation_product(job.pk,'sample')
            self.assertEqual(select.call_args.args[-1],Decimal(16))
            self.assertIn('Диагональ',select.call_args.args[1][0]['text'])
        job.refresh_from_db();self.assertEqual(job.state,'done')
        self.assertFalse(UserLotPrice.objects.exists())


class PreparationFieldTests(SimpleTestCase):
    def test_explicit_fields_and_canonical_country(self):
        from .overshop import preparation_fields
        self.assertEqual(preparation_fields({'name':'Товар','attributes':{'Производитель':'Тестовый завод','Страна производства':'Китай'}}),{'product_name':'Товар','manufacturer':'Тестовый завод','country':'КИТАЙ'})

    def test_brand_and_unknown_country_are_not_guessed(self):
        from .overshop import preparation_fields
        result=preparation_fields({'name':'China Brand','attributes':{'Бренд':'Brand','Страна производства':'Неизвестно'}})
        self.assertEqual(result['manufacturer'],'');self.assertEqual(result['country'],'')
        result=preparation_fields({'attributes':{'Производитель':'A','Изготовитель':'B'}})
        self.assertEqual(result['manufacturer'],'')
