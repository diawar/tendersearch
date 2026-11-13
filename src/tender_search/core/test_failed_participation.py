from django.test import SimpleTestCase
from .outcome_source import parse_lot_result,ProtocolFormatError

BIN='241040001415'
OTHER='123456789012'

class FailedProcurementParticipationTests(SimpleTestCase):
    def protocol(self, accepted=0, rejected=0, supplier='', title='Товар'):
        return f'''Протокол об итогах 100-1
Лот № 101-ЗЦП1
Ценовые предложения потенциальных поставщиков, автоматически отклоненные веб-порталом: {rejected}
№ Наименование поставщика БИН (ИИН)
Потенциальными поставщиками представлены следующие ценовые предложения: {accepted}
№ Наименование поставщика БИН (ИИН)
{supplier}
Признать государственную закупку {title} по лоту № 101-ЗЦП1 несостоявшейся в связи с отсутствием представленных ценовых предложений
'''

    def test_explicit_zero_accepted_and_rejected_proves_absence_without_winner(self):
        result=parse_lot_result(self.protocol(title='Длинное описание '*20),'100-1','101-ЗЦП1',BIN)
        self.assertEqual(result.state,'not_listed');self.assertTrue(result.participants_complete)

    def test_missing_or_inconsistent_counts_do_not_prove_absence(self):
        text=self.protocol()
        for changed in (text.replace('Потенциальными поставщиками представлены следующие ценовые предложения: 0',''),
                        text.replace('отклоненные веб-порталом: 0','отклоненные веб-порталом: 1')):
            try:
                result=parse_lot_result(changed,'100-1','101-ЗЦП1',BIN)
            except ProtocolFormatError:
                continue  # An unreadable participant section is inconclusive, never absence.
            self.assertEqual(result.state,'unsuccessful');self.assertFalse(result.participants_complete)

    def test_own_participant_is_not_absence_and_other_participant_can_prove_absence(self):
        for identifier,expected in ((BIN,'unsuccessful'),(OTHER,'not_listed')):
            result=parse_lot_result(self.protocol(accepted=1,supplier=f'1   Компания              {identifier}            1000'),'100-1','101-ЗЦП1',BIN)
            self.assertEqual(result.state,expected)

    def test_other_lot_counts_and_ambiguous_winner_cannot_prove_absence(self):
        text=self.protocol().replace('101-ЗЦП1','102-ЗЦП1')
        with self.assertRaises(ProtocolFormatError):parse_lot_result(text,'100-1','101-ЗЦП1',BIN)
        text=self.protocol()+f'\nОпределить победителем по лоту № 101-ЗЦП1 : {OTHER}'
        with self.assertRaises(ProtocolFormatError):parse_lot_result(text,'100-1','101-ЗЦП1',BIN)
