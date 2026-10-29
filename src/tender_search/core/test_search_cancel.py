from types import SimpleNamespace
from unittest.mock import Mock
from django.contrib.auth import get_user_model
from django.test import TestCase
from .models import WebSearch
from .search_service import execute_search

class SearchCancellationTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user('cancel-owner')
        self.other=get_user_model().objects.create_user('cancel-other')
        self.client.force_login(self.owner)
        self.job=WebSearch.objects.create(owner=self.owner,specification={'terms':['test'],'pages':2})
    def test_cancel_is_owner_only_and_post_only(self):
        url=f'/search/{self.job.pk}/cancel/'
        self.assertEqual(self.client.get(url).status_code,405)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url).status_code,404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(url).status_code,302)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status,'cancelled')
        api=Mock()
        execute_search(self.job.pk,api)
        api.pages.assert_not_called()
    def test_worker_does_not_overwrite_cancel_or_process_returned_page(self):
        api=Mock()
        def pages(**kwargs):
            WebSearch.objects.filter(pk=self.job.pk).update(status='cancelled')
            yield SimpleNamespace(count=1,results=[{'id':1}],next_url=None)
            raise AssertionError('Another request after cancellation')
        api.pages.side_effect=pages
        execute_search(self.job.pk,api)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status,'cancelled')
        self.assertEqual(self.job.hits.count(),0)
