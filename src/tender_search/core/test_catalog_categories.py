from unittest.mock import patch
from bs4 import BeautifulSoup
from django.test import SimpleTestCase, override_settings
from .catalog_categories import category_path, parse_categories, prioritize_urls
from .overshop import parse_product
from .product_ai import select_catalogue_categories


class SiteCategoriesTests(SimpleTestCase):
    def test_taxonomy_uses_real_navigation_labels_and_hierarchy_only(self):
        soup=BeautifulSoup('''<div class="menu-item"><a href="/catalog/pc/">Компьютеры</a></div>
        <div class="menu-item"><a href="/catalog/pc/boards/">Материнские платы</a></div>
        <div class="menu-item"><a href="https://evil.test/catalog/pc/">Wrong</a></div>
        <div class="menu-item"><a href="/catalog/pc/123/">Товар</a></div>
        <a href="/catalog/fake/">Реклама</a>''','html.parser')
        result=parse_categories(soup)
        self.assertEqual(len(result),2)
        self.assertEqual(result[1]['label'],'Компьютеры / Материнские платы')
        for path in ['/catalog/../auth/','//evil.test/catalog/pc/','/catalog/pc/?q=test','/catalog/pc/#x']:
            self.assertIsNone(category_path(path))

    def test_selected_category_prioritizes_late_results_but_keeps_global_fallback(self):
        wrong=[f'https://over-shop.kz/catalog/accessories/{i}/' for i in range(1,13)]
        relevant=[f'https://over-shop.kz/catalog/monitors/{i}/' for i in range(20,32)]
        selected=prioritize_urls(wrong+relevant,['/catalog/monitors/'])
        self.assertEqual(selected[:12],relevant)
        self.assertTrue(any(url in wrong for url in selected))
        self.assertEqual(prioritize_urls(wrong,[]),wrong)

    def test_ambiguous_categories_are_interleaved_without_starvation(self):
        first=[f'https://over-shop.kz/catalog/a/{i}/' for i in range(1,20)]
        second=[f'https://over-shop.kz/catalog/b/{i}/' for i in range(20,30)]
        selected=prioritize_urls(first+second,['/catalog/a/','/catalog/b/'])
        self.assertEqual(selected[:4],[first[0],second[0],first[1],second[1]])

    def test_product_category_ignores_unrelated_links_in_breadcrumb_dropdown(self):
        soup=BeautifulSoup('''<h1>Плата</h1><div class="breadcrumbs">
        <a href="/catalog/pc/">Компьютеры</a><a href="/catalog/monitors/">Мониторы</a>
        <a href="/catalog/pc/boards/">Платы</a></div>''','html.parser')
        product=parse_product(soup,'https://over-shop.kz/catalog/pc/boards/123/')
        self.assertEqual(product['category_label'],'Компьютеры / Платы')


@override_settings(OPENAI_API_KEY='test',OPENAI_MODEL='test-model',OPENAI_CHAT_ONLY=False)
class CategorySelectionTests(SimpleTestCase):
    def setUp(self):
        self.categories=[{'path':'/catalog/pc/','label':'Компьютеры','name':'Компьютеры'}]
        self.plan={'summary':'ПК','queries':['ПК'],'requirements':[{'scope':'product','label':'Компьютер'}]}

    def test_only_site_paths_are_accepted_and_choice_is_reused(self):
        with patch('tender_search.core.product_ai.load_categories',return_value=self.categories),patch('tender_search.core.product_ai.structured_response',return_value={'paths':['/catalog/pc/'],'state':'focused'}) as ai:
            first=select_catalogue_categories('ПК',self.plan)
            second=select_catalogue_categories('ПК',self.plan,first)
            self.assertEqual(first,second);self.assertEqual(ai.call_count,1)
            self.assertEqual(first['labels'],['Компьютеры'])

    def test_inventory_change_invalidates_category_choice(self):
        with patch('tender_search.core.product_ai.load_categories',return_value=self.categories),patch('tender_search.core.product_ai.structured_response',return_value={'paths':['/catalog/pc/'],'state':'focused'}) as ai:
            first=select_catalogue_categories('ПК',self.plan)
            self.categories.append({'path':'/catalog/laptops/','label':'Ноутбуки','name':'Ноутбуки'})
            select_catalogue_categories('ПК',self.plan,first)
            self.assertEqual(ai.call_count,2)

    def test_invalid_category_and_unavailable_taxonomy_preserve_global_search(self):
        with patch('tender_search.core.product_ai.load_categories',return_value=self.categories),patch('tender_search.core.product_ai.structured_response',return_value={'paths':['https://evil.test/'],'state':'focused'}):
            self.assertEqual(select_catalogue_categories('ПК',self.plan)['paths'],[])
        with patch('tender_search.core.product_ai.load_categories',side_effect=ValueError('unavailable')),patch('tender_search.core.product_ai.structured_response') as ai:
            self.assertEqual(select_catalogue_categories('ПК',self.plan)['paths'],[])
            ai.assert_not_called()
