import httpx
from unittest.mock import Mock
from django.test import SimpleTestCase
from tender_search.client import ZakupClient,SourceError
from .lead_source import LeadPortal

class PublicProtocolTransportTests(SimpleTestCase):
    def test_public_redirect_is_followed_without_browser_session(self):
        seen=[]
        def handle(request):
            seen.append(str(request.url))
            if request.url.host=='goszakup.gov.kz':
                return httpx.Response(301,headers={'Location':'https://old.goszakup.gov.kz/ru/announce/index/123?tab=protocols'})
            return httpx.Response(200,content=b'public protocols')
        with ZakupClient(transport=httpx.MockTransport(handle),interval=0,attempts=1) as api:
            self.assertEqual(LeadPortal(api).read('https://goszakup.gov.kz/ru/announce/index/123?tab=protocols'),b'public protocols')
        self.assertEqual(len(seen),2)

    def test_login_external_and_cabinet_redirects_never_get_requested(self):
        for target in ('/ru/user/login','https://example.org/ru/announce/index/123','https://old.goszakup.gov.kz/ru/cabinet/profile'):
            handler=Mock(return_value=httpx.Response(302,headers={'Location':target}))
            with ZakupClient(transport=httpx.MockTransport(handler),interval=0,attempts=1) as api:
                with self.assertRaises(SourceError):LeadPortal(api).read('https://old.goszakup.gov.kz/ru/announce/index/123')
            self.assertEqual(handler.call_count,1)

    def test_redirect_loop_is_bounded(self):
        handler=Mock(return_value=httpx.Response(301,headers={'Location':'/ru/announce/index/123'}))
        with ZakupClient(transport=httpx.MockTransport(handler),interval=0,attempts=1) as api:
            with self.assertRaises(SourceError):LeadPortal(api).read('https://old.goszakup.gov.kz/ru/announce/index/123')
        self.assertEqual(handler.call_count,3)
