from decimal import Decimal
from django.test import TestCase, override_settings
from .models import ApplicationPreparation, UserLotPrice, CompanySettings, UserLotWorkflow
from .lot_pricing import LotPriceForm, price_budget


@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class PreparationPriceTests(TestCase):
    def setUp(self):
        from .test_preparation import PreparationTests
        PreparationTests.setUp(self)
        self.lot.amount=Decimal('300.00');self.lot.save()
        self.detail['plan_items'][0]['unit_price']=100
        self.cache.rows=[self.detail];self.cache.save()
        self.company=CompanySettings.objects.get(owner=self.owner)

    def save_price(self,value,confirm=False,**extra):
        return self.client.post(self.url,dict(self.data,unit_gross=value,confirm_discount='on' if confirm else '',**extra))

    def test_one_form_and_prominent_budget_including_epz(self):
        self.lot.announcement.raw={'system':{'id':4}};self.lot.announcement.save()
        self.assertEqual(price_budget(self.lot)['unit'],100)
        page=self.client.get(self.url)
        self.assertContains(page,'Бюджет за единицу без НДС')
        self.assertContains(page,'Количество')
        self.assertContains(page,'Сохранить товар, цену и реквизиты',count=1)
        self.assertNotContains(page,'Сохранить цену')
        self.assertNotContains(page,'Сохранить данные')
        self.assertContains(page,'id="preparation-form"',count=1)

    def test_vat_payer_equality_and_over_budget_atomic_save(self):
        self.assertEqual(self.save_price('116').status_code,302)
        price=UserLotPrice.objects.get(owner=self.owner,lot=self.lot)
        self.assertEqual(price.unit_gross,116);self.assertEqual(price.vat_rate,16)
        prep=ApplicationPreparation.objects.get(owner=self.owner,lot=self.lot)
        self.assertEqual(prep.data['price_confirmation']['budget_total'],'300.00')
        before=prep.updated_at
        response=self.save_price('116.01',product_name='Не сохранять')
        self.assertContains(response,'превышает бюджет')
        prep.refresh_from_db();price.refresh_from_db()
        self.assertEqual(prep.updated_at,before);self.assertEqual(price.unit_gross,116)
        self.assertEqual(UserLotWorkflow.objects.get(owner=self.owner,lot=self.lot).stage,'preparing')
        self.assertIsNone(price.locked_at)

    def test_non_payer_no_vat_deduction_or_posted_rate_override(self):
        self.company.is_vat_payer=False;self.company.save()
        page=self.client.get(self.url)
        self.assertContains(page,'Наша цена за единицу без НДС')
        self.assertEqual(self.save_price('100',vat_rate='16').status_code,302)
        price=UserLotPrice.objects.get(owner=self.owner,lot=self.lot)
        self.assertEqual(price.vat_rate,0)
        self.assertContains(self.save_price('100.01',vat_rate='16'),'превышает бюджет')

    def test_discount_requires_confirmation_for_both_tax_modes(self):
        for payer,amount,above in [(True,'104.40','104.41'),(False,'90','90.01')]:
            self.company.is_vat_payer=payer;self.company.save()
            self.assertContains(self.save_price(amount),'подтвердите цену')
            self.assertEqual(self.save_price(amount,True).status_code,302)
            self.assertEqual(self.save_price(above).status_code,302)

    def test_invalid_product_does_not_save_price(self):
        self.save_price('116',product_name='')
        self.assertFalse(UserLotPrice.objects.exists())
        self.assertFalse(ApplicationPreparation.objects.exists())

    def test_uncertain_budget_and_total_rounding_cannot_exceed(self):
        self.lot.amount=Decimal('299.99');self.lot.save()
        self.assertContains(self.save_price('116'),'превышает бюджет')
        self.lot.raw['system_id']=2;self.lot.save()
        self.assertContains(self.save_price('100',True),'Бюджет без НДС и количество не подтверждены')

    def test_old_price_endpoint_also_checks_budget_and_confirmation(self):
        self.assertEqual(self.client.post(self.endpoint,{'unit_gross':'117'}).status_code,400)
        self.assertEqual(self.client.post(self.endpoint,{'unit_gross':'104.40'}).status_code,400)
        self.assertEqual(self.client.post(self.endpoint,{'unit_gross':'104.40','confirm_discount':'on'}).status_code,302)
