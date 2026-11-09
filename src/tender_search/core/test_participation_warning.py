from types import SimpleNamespace
from django.test import SimpleTestCase
from .templatetags.search_tags import participation_warning


class ParticipationWarningTests(SimpleTestCase):
    def lot(self, source='zakup', raw=None):
        return SimpleNamespace(raw=raw or {}, announcement=SimpleNamespace(source=source, raw={}), portal_url='https://zakup.gov.kz/announcement/1')

    def test_missing_or_false_flags_do_not_claim_eligibility(self):
        for raw in ({}, {'is_national_regime':False}, {'purchase_limitation_sign':None}):
            self.assertEqual(participation_warning(self.lot(raw=raw)), '')

    def test_other_platforms_do_not_receive_goszakup_warning(self):
        self.assertEqual(participation_warning(self.lot(source='med_ecc')), '')
        self.assertEqual(participation_warning(self.lot(raw={'system_id':3})), '')

    def test_open_lot_is_not_marked_restricted(self):
        lot=self.lot();lot.portal_url='https://zakup.gov.kz/?x="onclick'
        lot.raw={'system_id':1,'status':{'id':7},'purchase_limitation_sign':None}
        self.assertEqual(participation_warning(lot), '')
