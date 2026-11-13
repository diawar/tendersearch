from io import BytesIO
from tempfile import TemporaryDirectory
from decimal import Decimal
from django.test import TestCase,override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from pypdf import PdfWriter
from .models import LotShipment,UserLotWorkflow,CompanySettings,UserLotPrice
from .workflow_state import update_workflow
from .fulfillment import advance

@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ShippingFlowTests(TestCase):
    def setUp(self):
        from .test_lot_pricing import LotPricingTests
        LotPricingTests.setUp(self)
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        config=override_settings(MEDIA_ROOT=self.tmp.name);config.enable();self.addCleanup(config.disable)
        update_workflow(self.owner.pk,self.lot.pk,stage='delivery')

    def stage(self):
        return UserLotWorkflow.objects.get(owner=self.owner,lot=self.lot).stage

    def pdf(self):
        writer=PdfWriter();writer.add_blank_page(width=100,height=100)
        output=BytesIO();writer.write(output)
        return SimpleUploadedFile('act.pdf',output.getvalue(),content_type='application/pdf')

    def test_courier_act_payment_and_private_download(self):
        url=reverse('shipment_save',args=[self.lot.pk])
        self.client.post(url,{'mode':'courier','courier':'CDEK','tracking_number':'TEST123'})
        shipment=LotShipment.objects.get(owner=self.owner,lot=self.lot)
        self.assertIsNotNone(shipment.shipped_at)
        self.assertEqual(shipment.tracking_number,'TEST123')
        self.assertEqual(self.stage(),'delivery')
        self.client.post(reverse('shipment_act',args=[self.lot.pk]),{'act':self.pdf()})
        self.assertEqual(self.stage(),'payment')
        shipment.refresh_from_db()
        download=reverse('shipment_act_download',args=[self.lot.pk])+f'?act={shipment.pk}'
        self.client.force_login(self.other)
        response=self.client.get(download)
        self.assertEqual(response.status_code,404)
        from .shipping import context
        self.assertFalse(context(self.other,self.lot)['personal_acts'].exists())
        self.assertEqual(list(context(self.owner,self.lot)['personal_acts']),[shipment])
        self.assertNotContains(self.client.get(reverse('lot_detail',args=[self.lot.pk])),download)
        self.client.force_login(self.owner)
        response=self.client.get(download)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response['Cache-Control'],'no-store, private')
        self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF-'))
        self.assertNotEqual(UserLotWorkflow.objects.get(owner=self.other,lot=self.lot).stage,'payment')
        with self.assertRaises(ValueError):advance(self.owner.pk,self.lot.pk,'paid')
        self.assertEqual(self.stage(),'payment')
        self.assertTrue(LotShipment.objects.get(pk=shipment.pk).act)

    def test_two_users_have_separate_acts_for_same_lot(self):
        from django.utils import timezone
        from .shipping import context
        first=LotShipment.objects.create(owner=self.owner,lot=self.lot,act=self.pdf(),act_uploaded_at=timezone.now())
        second=LotShipment.objects.create(owner=self.other,lot=self.lot,act=self.pdf(),act_uploaded_at=timezone.now())
        self.assertNotEqual(first.act.name,second.act.name)
        self.assertEqual(list(context(self.owner,self.lot)['personal_acts']),[first])
        self.assertEqual(list(context(self.other,self.lot)['personal_acts']),[second])
        base=reverse('shipment_act_download',args=[self.lot.pk])
        self.assertEqual(self.client.get(base+f'?act={second.pk}').status_code,404)
        self.client.force_login(self.other)
        response=self.client.get(base+f'?act={second.pk}')
        self.assertEqual(response.status_code,200)
        self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF-'))
        for value in ('','invalid','9'*100):
            self.assertEqual(self.client.get(base+'?act='+value).status_code,404)

    def test_invalid_upload_and_act_before_dispatch_cannot_move_stage(self):
        endpoint=reverse('shipment_act',args=[self.lot.pk])
        self.client.post(endpoint,{'act':self.pdf()})
        self.assertEqual(self.stage(),'delivery')
        self.assertFalse(LotShipment.objects.exists())
        self.client.post(reverse('shipment_save',args=[self.lot.pk]),{'mode':'own'})
        self.client.post(endpoint,{'act':SimpleUploadedFile('act.pdf',b'<script>bad</script>')})
        self.assertEqual(self.stage(),'delivery')
        self.assertFalse(LotShipment.objects.get().act)
        with self.assertRaises(ValueError):advance(self.owner.pk,self.lot.pk,'payment')

    def test_other_user_cannot_change_personal_delivery(self):
        self.client.force_login(self.other)
        self.client.post(reverse('shipment_save',args=[self.lot.pk]),{'mode':'own'})
        self.assertFalse(LotShipment.objects.exists())

    def test_zero_vat_ignores_submitted_rate_and_locked_price_keeps_rate(self):
        update_workflow(self.owner.pk,self.lot.pk,stage='preparing')
        company=CompanySettings.objects.create(owner=self.owner,is_vat_payer=False)
        self.client.post(self.endpoint,{'confirm_discount':'on','unit_gross':'116','vat_rate':'16'})
        price=UserLotPrice.objects.get(owner=self.owner,lot=self.lot)
        self.assertEqual(price.vat_rate,0)
        from .lot_pricing import pricing_context
        self.assertEqual(pricing_context(self.owner,self.lot)['price_result']['total_net'],Decimal('348'))
        company.is_vat_payer=True;company.save()
        # Once fixed for a submitted bid, company setting changes cannot rewrite its tax basis.
        from django.utils import timezone
        price.locked_at=timezone.now();price.save()
        self.assertFalse(pricing_context(self.owner,self.lot)['price_has_vat'])
