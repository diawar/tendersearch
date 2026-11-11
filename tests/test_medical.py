from pathlib import Path
from unittest.mock import Mock
import httpx
import pytest
from tender_search.client import SchemaError, SourceError
from tender_search.medical import MedicalClient, parse_card, parse_lots, enrich_lot, parse_listing

ROOT = Path(__file__).parent/'fixtures/medical'


def fixture(name):
    return (ROOT/name).read_text(encoding='utf-8')


@pytest.mark.parametrize('host,source,aid,lid,amount', [
    ('med.ecc.kz', 'med_ecc', 2021878, 17596482, '3800000'),
    ('fms.ecc.kz', 'fms_ecc', 525724, 4886024, '1657115369.47')])
def test_real_tables_and_details(host, source, aid, lid, amount):
    card = parse_card(fixture(host+'-card.html'), aid, source)
    rows = parse_lots(fixture(host+'-lots.html'), card)
    assert len(rows) == 1
    row = enrich_lot(rows[0], fixture(host+'-lot-detail.html'))
    assert row['id'] == lid and row['total_price'] == amount
    assert row['_source'] == source and row['status']['id'] == 7
    assert row['purchase_subject_id'] == ([1] if source == 'med_ecc' else [3])
    assert row['offer_end_date'].endswith('+05:00')
    if source == 'med_ecc':
        assert 'электрокардиограммы' in row['description_ru']
        assert 'Медициналық' in row['description_ru']
        assert row['_has_advance'] is False


def test_drug_columns_differ_from_logistics():
    card = parse_card(fixture('fms-drugs-card.html'), 526824, 'fms_ecc')
    rows = parse_lots(fixture('fms-drugs-lots.html'), card)
    assert [r['name_ru'] for r in rows] == ['Йогексол', 'Йогексол']
    assert rows[0]['total_price'] == '11921677.2'
    assert rows[0]['customers'][0]['bin'] == '090340007747'
    assert rows[0]['purchase_subject_id'] == [1]


@pytest.mark.parametrize('host', ['med.ecc.kz', 'fms.ecc.kz'])
def test_active_listing_pagination_and_filter_verification(host):
    html = fixture(host+'-active.html')
    rows, next_page = parse_listing(html, 1, 220)
    assert len(rows) == 10 and next_page == 2
    with pytest.raises(SchemaError):
        parse_listing(html, 1, 210)
    with pytest.raises(SchemaError):
        parse_listing('<html>Войти</html>', 1, 220)


def test_known_empty_layout_only():
    html = '<form id="formFilter"><select name="statuses"><option selected value="210">Опубликовано</option></select></form><div class="row"><div class="col-md-12"> </div></div>'
    assert parse_listing(html, 1, 210) == ([], None)
    with pytest.raises(SchemaError): parse_listing(html, 1, 220)
    with pytest.raises(SchemaError): parse_listing('<h1>Ошибка</h1>', 1, 210)


def test_mismatched_card_and_missing_lots_fail_closed():
    with pytest.raises(SchemaError): parse_card(fixture('med.ecc.kz-card.html'), 1, 'med_ecc')
    card = parse_card(fixture('med.ecc.kz-card.html'), 2021878, 'med_ecc')
    with pytest.raises(SchemaError): parse_lots('<table></table>', card)
    row = parse_lots(fixture('med.ecc.kz-lots.html'), card)[0]
    with pytest.raises(SchemaError): enrich_lot(row, fixture('fms.ecc.kz-lot-detail.html'))


def test_retries_are_read_only_and_file_hosts_restricted():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(503 if len(calls)==1 else 200, text='<html>ok</html>', headers={'Retry-After':'0'})
    with MedicalClient('med_ecc', transport=httpx.MockTransport(handler), interval=0) as api:
        assert api.html('/ru/searchanno') == '<html>ok</html>'
        assert len(calls) == 2 and all(r.method == 'GET' for r in calls)
        for url in ['https://evil.invalid/files/download_file/1/1/', 'http://med.ecc.kz/files/download_file/1/1/', 'https://med.ecc.kz/ru/user/login']:
            with pytest.raises(SourceError): api._file_url(url)
        with pytest.raises(SourceError):
            api._file_url('https://med.ecc.kz/files/download_file/1/1/', resolver=lambda *a,**kw:[(None,None,None,None,('127.0.0.1',443))])


def test_all_lot_pages_and_repeated_page_detection():
    card = parse_card(fixture('med.ecc.kz-card.html'), 2021878, 'med_ecc')
    card['lot_count'] = 2
    first = fixture('med.ecc.kz-lots.html') + '<div class="pagination"><a href="?tab=lots&page=2">2</a></div>'
    second = fixture('med.ecc.kz-lots.html').replace('17596482', '17596483')
    detail = fixture('med.ecc.kz-lot-detail.html')
    with MedicalClient('med_ecc', interval=0) as api:
        api.cards[2021878] = card
        api.html = Mock(side_effect=[first,second,detail,first,detail.replace('17596482','17596483')])
        rows = api.announcement_lots(2021878)
        assert [r['id'] for r in rows] == [17596482,17596483]
        assert api.html.call_args_list[1].kwargs['params']['page'] == 2
        api.html = Mock(side_effect=[first,first])
        with pytest.raises(SchemaError, match='повторил'): api.announcement_lots(2021878)


def test_healthcare_lot_columns():
    card = parse_card(fixture('fms.ecc.kz-card.html'), 525724, 'fms_ecc')
    rows = parse_lots(fixture('fms-healthcare-lots.html'), card)
    assert len(rows) == 1
    assert rows[0]['id'] == 4828503
    assert rows[0]['lot_number'] == '4804891-Зму3'
    assert rows[0]['name_ru'] == 'Высокотехнологичная медицинская помощь'
    assert 'ОСМС' in rows[0]['description_ru']
    assert rows[0]['total_price'] == '5213445405.26'
    assert rows[0]['status']['id'] == 7
