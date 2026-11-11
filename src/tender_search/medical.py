"""Read-only public HTML/AJAX adapter for ECC medical procurement portals.

Source identifiers and status names are retained; normalized codes are used only
for the application's common filters. No Tenderbot credentials or API is used.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import ipaddress
import re
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit, parse_qs
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import certifi
import httpx

from .client import ZakupClient, SourceError, SchemaError, NotFound, Document, positive_id

HOSTS = {'med_ecc': 'med.ecc.kz', 'fms_ecc': 'fms.ecc.kz'}
ALMATY = ZoneInfo('Asia/Almaty')
ACTIVE = (210, 220)


def text(node):
    return ' '.join(node.get_text(' ', strip=True).split())


def fields(soup):
    result = {}
    for row in soup.select('tr'):
        cells = row.find_all(['th', 'td'], recursive=False)
        if len(cells) == 2:
            result[text(cells[0])] = text(cells[1])
    for label in soup.select('.form-group > label'):
        value = label.parent.select_one('input[readonly],textarea[readonly]')
        if value:
            result[text(label)] = value.get('value', text(value))
    return result


def money(value):
    value = re.sub(r'\s+', '', value or '')
    if not value or value == '-':
        return None
    if '.' in value:
        value = value.replace(',', '')
    else:
        value = value.replace(',', '.')
    try:
        number = Decimal(value)
        if not number.is_finite() or number < 0:
            raise InvalidOperation
        return str(number)
    except InvalidOperation as exc:
        raise SchemaError('Изменился формат суммы медицинской закупки') from exc


def date(value):
    if not value or value == '-':
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=ALMATY).isoformat()
    except ValueError as exc:
        raise SchemaError('Изменился формат даты медицинской закупки') from exc


def status(name):
    value = name.casefold().replace('ё', 'е')
    if 'прием заявок' in value: code = 7
    elif value in ('опубликован', 'опубликовано'): code = 6
    elif 'дополн' in value: code = 8
    else: code = 0  # retain the name; never mislabel an unknown status as open
    return {'id': code, 'name': name}


def parse_listing(html, page, expected_status):
    soup = BeautifulSoup(html, 'html.parser')
    table = next((t for t in soup.select('table') if 'Название объявления' in text(t)), None)
    if table is None:
        # ECC's successful zero-result response omits the table entirely. Require
        # the selected filter and the known empty results container, not HTTP 200 alone.
        selected = soup.select_one('form#formFilter select[name="statuses"] option[selected]')
        empty_result = any(not text(col) and not col.find(True) for col in soup.select('div.row > div.col-md-12'))
        if selected and selected.get('value') == str(expected_status) and empty_result and not soup.select('.pagination a[href]'):
            return [], None
        raise SchemaError('Не найдена таблица реестра медицинских закупок')
    headings = [text(c) for c in table.select('tr')[0].find_all(['th', 'td'], recursive=False)]
    result = []
    for tr in table.select('tr')[1:]:
        link = tr.select_one('a[href*="/announce/index/"]')
        if not link:
            continue
        values = dict(zip(headings, (text(c) for c in tr.find_all('td', recursive=False))))
        sid = status(values.get('Статус', ''))['id']
        if sid != {210: 6, 220: 7}[expected_status]:
            raise SchemaError('Медицинский портал не применил фильтр статуса')
        match = re.search(r'/announce/index/(\d+)', link['href'])
        result.append({'id': int(match[1]), 'end': date(values.get('Дата окончания приема заявок'))})
    pages = [int(parse_qs(urlsplit(a['href']).query).get('page', ['1'])[0])
             for a in soup.select('.pagination a[href]')]
    next_page = page + 1 if any(p > page for p in pages) else None
    if not result and next_page:
        raise SchemaError('Пустая страница медицинского реестра с продолжением')
    return result, next_page


def parse_card(html, identifier, source):
    f = fields(BeautifulSoup(html, 'html.parser'))
    number = f.get('Номер объявления', '')
    if number.split('-')[0] != str(identifier) or not f.get('Статус объявления'):
        raise SchemaError('Карточка медицинской закупки не соответствует номеру')
    customer = f.get('Заказчик') or f.get('Единый дистрибьютор', '')
    match = re.match(r'(\d{12})\s*(.*)', customer)
    return {'id': identifier, '_source': source, 'number': number,
            'name_ru': f.get('Наименование объявления', ''),
            'status': status(f['Статус объявления']),
            'offer_start_date': date(f.get('Срок начала приема заявок')),
            'offer_end_date': date(f.get('Срок окончания приема заявок')),
            'publish_date': date(f.get('Дата публикации объявления')),
            'purchase_method': {'name': f.get('Способ проведения закупки', '')},
            'purchase_subject_id': subject_code(f.get('Вид предмета закупок', '')),
            'lot_count': int(f.get('Кол-во лотов в объявлении', '0')),
            'customers': [{'id': int(match[1]), 'bin': match[1], 'name_ru': match[2]}] if match else [],
            '_medical': True}


def subject_code(name):
    code = {'товар': 1, 'товары': 1, 'работа': 2, 'работы': 2, 'услуга': 3, 'услуги': 3}.get(name.strip().casefold())
    return [code] if code else []


def parse_lots(html, card, *, check_count=True):
    soup = BeautifulSoup(html, 'html.parser')
    result = []
    for link in soup.select('[data-lot-id]'):
        tr = link.find_parent('tr')
        if tr is None:
            raise SchemaError('Не найдена строка медицинского лота')
        table = tr.find_parent('table')
        headers = [text(c) for c in table.select('tr')[0].find_all(['th', 'td'], recursive=False)]
        values = dict(zip(headers, (text(c) for c in tr.find_all('td', recursive=False))))
        name = values.get('Наименование') or values.get('МНН') or values.get('Наименование лота') or values.get('Форма/Вид медицинской помощи')
        state_name = values.get('Статус') or values.get('Статус лота')
        if not name or not state_name:
            raise SchemaError('Изменились столбцы медицинского лота')
        identifier = positive_id(link['data-lot-id'])
        customer = values.get('Заказчик', '')
        match = re.match(r'(\d{12})[,\s]+(.*)', customer)
        customers = [{'id': int(match[1]), 'bin': match[1], 'name_ru': match[2]}] if match else card['customers']
        result.append({'id': identifier, '_source': card['_source'], '_medical': True,
            'announcement_id': card['id'], 'announcement_number': card['number'],
            'announcement_name_ru': card['name_ru'], '_announcement': card,
            'lot_number': text(link), 'name_ru': name,
            'description_ru': values.get('Краткая характеристика (описание) предмета закупки', values.get('Лекарственная форма', values.get('Подвиды медицинской помощи', ''))),
            'total_price': money(values.get('Общая сумма закупа (в тенге)', values.get('Сумма', values.get('Общая сумма лота')))),
            'status': status(state_name), 'customers': customers,
            'purchase_method': card['purchase_method'], 'purchase_method_name': card['purchase_method']['name'],
            'purchase_subject_id': subject_code(values.get('Вид предмета закупки', '')) or card.get('purchase_subject_id', []),
            'offer_start_date': card['offer_start_date'], 'offer_end_date': card['offer_end_date'],
            'publish_date': card['publish_date'], 'announcement_publish_date': card['publish_date'], 'delivery_addresses': [],
            '_delivery_text': values.get('Место оказания услуги', '')})
    result = list({r['id']: r for r in result}.values())
    if check_count and len(result) != card['lot_count']:
        raise SchemaError('Получены не все лоты медицинского объявления')
    return result


def enrich_lot(row, html):
    soup = BeautifulSoup(html, 'html.parser')
    f = fields(soup)
    if f.get('Лот №') != row['lot_number']:
        raise SchemaError('Подробности медицинского лота не соответствуют номеру')
    descriptions = [row['description_ru']] + [v for k, v in f.items() if 'характеристика' in k.casefold()]
    row['description_ru'] = '\n'.join(dict.fromkeys(v for v in descriptions if v))
    row['_extended_description'] = True
    row['_has_advance'] = Decimal(money(f.get('Размер авансового платежа, %')) or '0') > 0
    addresses = [f.get('Место оказания услуги', '')]
    for table in soup.select('table'):
        headers = table.select('tr')[0].find_all(['th', 'td'], recursive=False)
        if headers and text(headers[0]) == 'Адрес поставки':
            addresses.extend(text(tr.select('td')[0]) for tr in table.select('tr')[1:] if tr.select('td'))
    row['_delivery_text'] = '\n'.join(v for v in addresses if v)
    return row


class MedicalClient(ZakupClient):
    """Reuse bounded download/retry/pacing primitives, with a separate host allowlist."""
    def __init__(self, source, *, transport=None, gate=None, interval=1):
        self.source = source
        self.host = HOSTS[source]
        super().__init__(gate=gate, interval=interval)
        self.http.close()
        context = ssl.create_default_context(cafile=certifi.where())
        # Public intermediate verified against the OS trust chain on 30 Oct 2026.
        context.load_verify_locations(Path(__file__).parent/'certs/ecc-intermediate.crt')
        self.http = httpx.Client(base_url='https://'+self.host, verify=context, transport=transport,
            timeout=httpx.Timeout(45, connect=10), follow_redirects=False,
            headers={'User-Agent': 'GoszakupOver/0.1', 'Accept': 'text/html'})
        self.cards = {}
        self.heartbeat = None

    def html(self, path, *, data=None, params=None):
        if not re.fullmatch(r'/ru/(?:searchanno|announce/index/\d+|announce/ajax_load_lot/\d+|announce/actionAjaxModalShowFiles/\d+/\d+)', path):
            raise SourceError('Недопустимый адрес медицинского источника')
        if data is not None and not re.fullmatch(r'/ru/announce/ajax_load_lot/\d+', path):
            raise SourceError('Допускается только чтение подробностей лота')
        for attempt in range(3):
            self._pace()
            if self.heartbeat:
                self.heartbeat()
            try:
                with self.http.stream('POST' if data is not None else 'GET', path, data=data, params=params,
                        headers={'X-Requested-With': 'XMLHttpRequest', 'Referer': 'https://'+self.host+'/ru/searchanno'}) as response:
                    if response.status_code == 404:
                        raise NotFound(f'{self.host}: HTTP 404')
                    if response.status_code == 429 or response.status_code >= 500:
                        self.sleep(self._delay(response, attempt))
                        continue
                    if response.status_code != 200:
                        raise SourceError(f'{self.host}: HTTP {response.status_code}')
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 12*1024*1024:
                            raise SourceError('Страница медицинского портала превышает лимит размера')
                        chunks.append(chunk)
                    return b''.join(chunks).decode('utf-8-sig')
            except httpx.TransportError:
                self.sleep(2**attempt)
        raise SourceError(f'{self.host}: временно недоступен; повторим страницу позже')

    def listing(self, page=1, status_id=220):
        return parse_listing(self.html('/ru/searchanno', params={'statuses': status_id, 'page': page}), page, status_id)

    def announcement(self, identifier):
        identifier = positive_id(identifier)
        card = parse_card(self.html(f'/ru/announce/index/{identifier}'), identifier, self.source)
        self.cards[identifier] = card
        return card

    def announcement_lots(self, identifier):
        identifier = positive_id(identifier)
        card = self.cards.get(identifier) or self.announcement(identifier)
        rows, page = [], 1
        seen = set()
        while True:
            html = self.html(f'/ru/announce/index/{identifier}', params={'tab': 'lots', 'page': page})
            batch = parse_lots(html, card, check_count=False)
            if any(row['id'] in seen for row in batch):
                raise SchemaError('Портал повторил страницу медицинских лотов')
            rows.extend(batch)
            seen.update(row['id'] for row in batch)
            soup = BeautifulSoup(html, 'html.parser')
            pages = [int(parse_qs(urlsplit(a['href']).query).get('page', ['1'])[0]) for a in soup.select('.pagination a[href]')]
            if not any(p > page for p in pages):
                break
            if not batch or page >= card['lot_count'] or page >= 10000:
                raise SchemaError('Некорректное продолжение списка медицинских лотов')
            page += 1
        if len(rows) != card['lot_count']:
            raise SchemaError('Получены не все лоты медицинского объявления')
        for row in rows:
            # Obtain a fresh anonymous CSRF token for every POST (rotation is allowed).
            if row is not rows[0]:
                html = self.html(f'/ru/announce/index/{identifier}', params={'tab': 'lots'})
            meta = BeautifulSoup(html, 'html.parser').select_one('meta[name="ci_csrf_token"]')
            data = {'id': row['id']}
            if meta and meta.get('content'):
                data['ci_csrf_token'] = meta['content']
            enrich_lot(row, self.html(f'/ru/announce/ajax_load_lot/{identifier}', data=data))
        return rows

    def documents(self, identifier):
        identifier = positive_id(identifier)
        html = self.html(f'/ru/announce/index/{identifier}', params={'tab': 'documents'})
        groups = set(re.findall(r'actionModalShowFiles\(\s*[\'"]?(\d+)[\'"]?\s*,\s*[\'"]?(\d+)', html))
        if 'Документация' not in html and 'Документы' not in html:
            raise SchemaError('Не найден раздел медицинских документов')
        result = {}
        for aid, group in groups:
            if int(aid) != identifier:
                raise SchemaError('Документы другого медицинского объявления')
            soup = BeautifulSoup(self.html(f'/ru/announce/actionAjaxModalShowFiles/{aid}/{group}'), 'html.parser')
            for a in soup.select('a[href*="/files/download_file/"]'):
                url = urljoin('https://'+self.host, a['href'])
                self._file_url(url)
                file_id = urlsplit(url).path.strip('/').replace('/', ':')
                result[file_id] = Document(file_id, text(a) or 'document.pdf', url, identifier, None, 'Документы закупки')
        return list(result.values())

    def _file_url(self, url, resolver=socket.getaddrinfo):
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.netloc != self.host or not re.fullmatch(r'/files/download_file/\d+/\d+/?', parts.path) or parts.query or parts.fragment:
            raise SourceError('Хост медицинского файла не входит в список разрешённых')
        try:
            addresses = resolver(self.host, 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise SourceError('Непубличный адрес медицинского файла')
        except OSError as exc:
            raise SourceError('Не удалось разрешить адрес медицинского файла') from exc
        return url
