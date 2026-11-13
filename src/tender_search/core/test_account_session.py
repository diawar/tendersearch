from django.test import TestCase, Client, override_settings
from django.contrib.auth import get_user_model


@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},
    'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class AccountSessionTests(TestCase):
    def test_two_accounts_and_two_sessions_are_distinct_and_not_credentials(self):
        a=get_user_model().objects.create_user('account-a')
        b=get_user_model().objects.create_user('account-b')
        self.client.force_login(a)
        first=self.client.get('/account/session/')
        self.assertEqual(first.status_code,200)
        self.assertIn('no-store',first['Cache-Control'])
        identity=first.json()
        self.assertEqual(identity,self.client.get('/account/session/').json())
        self.assertNotIn(self.client.session.session_key,first.content.decode())
        second=Client();second.force_login(a)
        same_account=second.get('/account/session/').json()
        self.assertEqual(identity['account'],same_account['account'])
        self.assertNotEqual(identity['scope'],same_account['scope'])
        self.client.logout()
        self.assertFalse(self.client.get('/account/session/').json()['authenticated'])
        self.client.force_login(b)
        different=self.client.get('/account/session/').json()
        self.assertNotEqual(identity['account'],different['account'])
        self.assertNotEqual(identity['scope'],different['scope'])
        self.assertContains(self.client.get('/search/'),'data-account-scope="'+different['scope']+'"')
