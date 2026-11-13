from django.test import TestCase, Client, override_settings
from django.contrib.auth import get_user_model
from .models import SearchProfile


@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},
    'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ProfileRenameTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user('rename-owner')
        self.other=get_user_model().objects.create_user('rename-other')
        self.profile=SearchProfile.objects.create(owner=self.owner,name='Старое имя',include_terms=['монитор'],
            enabled=True,collection_enabled=True,filters={'subject':'1'})
        self.url=f'/profiles/{self.profile.pk}/rename/'
        self.client.force_login(self.owner)

    def test_only_name_changes_without_restarting_monitoring(self):
        before=SearchProfile.objects.values().get(pk=self.profile.pk)
        response=self.client.post(self.url,{'name':'  Мониторы для офиса  '})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['name'],'Мониторы для офиса')
        after=SearchProfile.objects.values().get(pk=self.profile.pk)
        before['name']=after['name']
        self.assertEqual(before,after)

    def test_owner_post_csrf_and_validation(self):
        self.assertEqual(self.client.get(self.url).status_code,405)
        for name in ['','  ','a'*201]:
            self.assertEqual(self.client.post(self.url,{'name':name}).status_code,400)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(self.url,{'name':'Чужое'}).status_code,404)
        strict=Client(enforce_csrf_checks=True);strict.force_login(self.owner)
        self.assertEqual(strict.post(self.url,{'name':'Без CSRF'}).status_code,403)
        self.profile.refresh_from_db();self.assertEqual(self.profile.name,'Старое имя')

    def test_control_on_search_and_workspace_and_safe_rendering(self):
        self.profile.name='<img src=x onerror=alert(1)>'
        self.profile.save(update_fields=['name'])
        for path in ['/search/',f'/workspace/?profile={self.profile.pk}']:
            response=self.client.get(path)
            self.assertEqual(response.status_code,200)
            self.assertContains(response,self.url)
            self.assertContains(response,'aria-label="Сохранить название"')
            self.assertContains(response,'&lt;img src=x onerror=alert(1)&gt;')
            self.assertNotContains(response,'<img src=x onerror=alert(1)>')
