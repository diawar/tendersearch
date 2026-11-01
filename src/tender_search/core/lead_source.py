"""Public EGZ protocols and identity-checked business contact cards."""
from dataclasses import dataclass, field
from io import BytesIO
import re
import unicodedata
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
import httpx
from pypdf import PdfReader

from tender_search.client import SourceError, NotFound


class ProtocolFormatError(SourceError):
    pass


def compact(value):
    return re.sub(r'\s+', ' ', value or '').strip()


@dataclass
class Participant:
    identifier: str
    name: str
    lots: set = field(default_factory=set)
    winner: bool = False


def parse_protocol_text(text):
    """Read supplier table rows, never arbitrary identifiers in the PDF body."""
    text = unicodedata.normalize('NFC', text).replace('\t', ' ')
    russian = re.search(r'Протокол\s+об\s+итогах', text, re.I)
    if russian:
        text = text[russian.start():]
    participants, headers = {}, []
    active, recognized, unresolved, lot = False, 0, 0, ''
    last, name_end, continuation_count = None, 0, 0
    for line in text.splitlines():
        plain = compact(line)
        if (re.match(r'^(?:Члены комиссии|Конкурсная комиссия|Наименование заказчика|Заказчик\b|Организатор\b)', plain, re.I)
                or (re.search(r'Ф\.?\s*И\.?\s*О\.?', plain) and re.search(r'ИИН|Должность', plain))):
            active, last, headers = False, None, []
        if plain.startswith(('Информация о ', 'Результаты соответствия')):
            active, last, headers = False, None, []
        lot_match = re.match(r'(?:Лот\s*№|Лоттың\s*№|№\s*лота)\s*:?\s*([^\s]+)', plain, re.I)
        if lot_match:
            lot = lot_match.group(1)
            active, last = False, None
        headers = (headers+[plain.lower()])[-5:]
        if re.search(r'БИН|ИИН|БСН|ЖСН', plain):
            header = ' '.join(headers)
            if (('наименование' in header and 'поставщик' in header)
                    or ('өнім беруш' in header and 'атауы' in header)):
                active, last = True, None
                recognized += 1
            else:
                active, last = False, None
        if re.match(r'^\d+\.\s', plain) or plain.startswith(('Лот №', 'Лоттың №')):
            active, last = False, None
        if not active:
            continue
        # Most protocols place the name before the identifier; some competition
        # templates reverse these columns. Both require a numbered table row.
        before = re.match(r'^\s*(?:\d{1,5}\s+)?(.+?)\s{2,}(\d{12})(?=\s|$)', line)
        after = re.match(r'^\s*(?:\d{1,5}\s+)?(\d{12})\s+(.+?)(?:\s{2,}|$)', line)
        if before or after:
            if after:
                identifier, name = after.group(1), compact(after.group(2))
                name_end = after.end(2)
            else:
                name, identifier = compact(before.group(1)), before.group(2)
                name_end = before.start(2)
            if not re.search(r'[^\W\d_]', name, re.UNICODE):
                unresolved += 1
                last = None
                continue
            item = participants.setdefault(identifier, Participant(identifier, name))
            if lot:
                item.lots.add(lot)
            # Prefer the longer spelling when the same company occurs again.
            if len(name) > len(item.name):
                item.name = name
            last, continuation_count = item, 0
        elif re.match(r'^\d{1,5}\s+\S', plain):
            unresolved += 1
            last = None
        elif last and plain and line[:1].isspace() and continuation_count < 4:
            prefix = compact(line[:name_end])
            indentation = len(line)-len(line.lstrip())
            if 2 <= indentation < name_end and prefix and not re.search(r'\d{8,}|БИН|ИИН', prefix):
                if prefix not in last.name and not prefix.startswith(('№', 'Цена', 'Сумма', 'Дата')):
                    last.name = compact(last.name+' '+prefix)
                    continuation_count += 1
        elif not plain:
            last = None
    flattened = compact(text)
    for match in re.finditer(r'Определить\s+победителем.{0,180}?:\s*(\d{12})', flattened, re.I):
        if match.group(1) in participants:
            participants[match.group(1)].winner = True
    if len(participants) == 1 and re.search(r'Решено\s+заключить\s+договор', flattened, re.I):
        next(iter(participants.values())).winner = True
    if not recognized:
        raise ProtocolFormatError('Не распознана таблица участников протокола. Нужна проверка формата.')
    if not participants and not re.search(r'(?:предложения|заявки|ұсынды)\s*:\s*0\b', flattened, re.I):
        raise ProtocolFormatError('В протоколе не удалось подтвердить список участников.')
    return list(participants.values()), unresolved


def parse_registry_card(html, identifier):
    soup = BeautifulSoup(html, 'html.parser')
    values = {}
    for tr in soup.select('tr'):
        cells = tr.find_all(['th', 'td'], recursive=False)
        if len(cells) >= 2:
            label = compact(cells[0].get_text(' ', strip=True)).rstrip(':').casefold()
            values[label] = compact(cells[1].get_text(' ', strip=True))
    bin_value, iin_value = values.get('бин участника', ''), values.get('иин участника', '')
    if identifier not in {bin_value, iin_value}:
        raise SourceError('Карточка поставщика не подтвердила БИН/ИИН. Контакты не сохранены.')
    emails = set()
    raw = values.get('e-mail', values.get('email', ''))
    for email in re.findall(r'[\w.!#$%&\x27*+/=?^`{|}~-]+@[\w.-]+\.[\w-]{2,}', raw):
        email = email.strip('.,;').casefold()
        try:
            validate_email(email)
        except ValidationError:
            continue
        emails.add(email)
    return {'name': values.get('наименование на рус. языке') or values.get('наименование на каз. языке') or '',
            'kind': 'bin' if bin_value == identifier else 'iin', 'emails': sorted(emails)}


def parse_protocol_html(html):
    soup = BeautifulSoup(html, 'html.parser')
    participants, recognized, unresolved = {}, 0, 0
    for table in soup.select('table'):
        name_column, id_column = None, None
        for row in table.find_all('tr'):
            cells = row.find_all(['td', 'th'], recursive=False)
            if not cells or any(cell.find('table') is not None for cell in cells):
                continue
            values = [compact(cell.get_text(' ', strip=True)) for cell in cells]
            names = [i for i, value in enumerate(values) if
                     ('наимен' in value.casefold() and any(term in value.casefold() for term in ['поставщик', 'участник']))
                     or ('өнім беруш' in value.casefold() and 'атауы' in value.casefold())]
            identifiers = [i for i, value in enumerate(values) if re.search(r'БИН|ИИН|БСН|ЖСН', value)]
            if names and identifiers:
                name_column, id_column = names[0], identifiers[0]
                recognized += 1
                continue
            if name_column is None or max(name_column, id_column) >= len(values):
                continue
            identifier_value, name = values[id_column], values[name_column]
            match = re.fullmatch(r'\d{12}', identifier_value)
            if name_column == id_column:
                match = re.search(r'\b\d{12}\b', identifier_value)
                name = compact(re.sub(r'\b\d{12}\b', '', name))
            if not match:
                if any(re.search(r'\d{8,}', value) for value in values):
                    unresolved += 1
                continue
            identifier = match.group(0)
            if not name:
                unresolved += 1
                continue
            person = participants.setdefault(identifier, Participant(identifier, name))
            if len(name) > len(person.name):
                person.name = name
            if re.search(r'признан.{0,20}победител', ' '.join(values), re.I):
                person.winner = True
    text = compact(soup.get_text(' ', strip=True))
    no_bids = bool(re.search(r'отсутстви[ея]\s+представленных\s+заявок|предложения\s*:\s*0|'
                            r'Информация о представленных заявках.{0,100}:\s*0\b', text, re.I))
    if not recognized and not no_bids:
        raise ProtocolFormatError('Не распознана таблица участников HTML-протокола')
    if not participants and not no_bids:
        raise ProtocolFormatError('В HTML-протоколе не подтверждён пустой список участников')
    return list(participants.values()), unresolved


class LeadPortal:
    def __init__(self, api):
        self.api = api

    def read(self, url, max_bytes=2*1024*1024):
        parts = urlsplit(url)
        allowed = (parts.hostname == 'old.goszakup.gov.kz' and re.fullmatch(
            r'/ru/(?:announce/index|registry/show_supplier)/\d+', parts.path.rstrip('/')))
        allowed = allowed or (parts.hostname == 'v3bl.goszakup.gov.kz' and re.fullmatch(
            r'/files/download_file/\d+/\d+/?', parts.path))
        if (not allowed or parts.scheme != 'https' or parts.username or parts.password
                or parts.port not in (None, 443) or parts.fragment):
            raise SourceError('Недопустимый адрес публичного источника контактов')
        for attempt in range(self.api.attempts):
            self.api._pace()
            try:
                with self.api.http.stream('GET', url, headers={'Accept': '*/*'}) as response:
                    if response.status_code == 404:
                        raise NotFound('Публичная карточка или протокол больше не доступны (HTTP 404)')
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt+1 < self.api.attempts:
                            self.api.sleep(self.api._delay(response, attempt))
                            continue
                    if response.status_code != 200:
                        raise SourceError(f'Источник контактов: HTTP {response.status_code}')
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > max_bytes:
                            raise ProtocolFormatError('Документ слишком большой для автоматического разбора')
                    return bytes(data)
            except httpx.TransportError as exc:
                if attempt+1 == self.api.attempts:
                    raise SourceError('Сетевая ошибка при чтении публичных итогов') from exc
                self.api.sleep(2**attempt)
        raise SourceError('Не удалось прочитать публичные итоги')

    def protocol(self, external_id):
        page_url = f'https://old.goszakup.gov.kz/ru/announce/index/{int(external_id)}?tab=protocols'
        soup = BeautifulSoup(self.read(page_url), 'html.parser')
        tables = []
        for heading in soup.find_all(string=re.compile(r'Протокол\s+итогов', re.I)):
            parent_table = heading.find_parent('table')
            table = parent_table if parent_table and parent_table.select('a[href]') else heading.find_next('table')
            if table is not None and all(table is not seen for seen in tables):
                tables.append(table)
        candidates = []
        for table in tables:
            for row in table.select('tr'):
                cells = row.find_all('td', recursive=False)
                if not cells:
                    continue
                status = compact(cells[-1].get_text(' ', strip=True)).casefold()
                if status != 'действительный':
                    continue
                for link in row.select('a[href]'):
                    if 'просмотреть протокол' not in compact(link.get_text()).casefold():
                        continue
                    date = compact(cells[1].get_text()) if len(cells) > 1 else ''
                    candidates.append((date, link['href']))
        if not candidates:
            raise SourceError('Действительный протокол итогов ещё не опубликован или недоступен')
        _, url = max(candidates)
        data = self.read(url, max_bytes=20*1024*1024)
        if b'<html' in data[:500].lower() or data.lstrip().lower().startswith(b'<!doctype html'):
            participants, unresolved = parse_protocol_html(data)
            return participants, unresolved, url
        if not data.startswith(b'%PDF-'):
            raise ProtocolFormatError('Источник вернул протокол не в поддерживаемом PDF/HTML-формате')
        try:
            reader = PdfReader(BytesIO(data))
            if reader.is_encrypted or len(reader.pages) > 150:
                raise ProtocolFormatError('PDF зашифрован или содержит более 150 страниц: нужна проверка')
            text = '\n'.join(page.extract_text(extraction_mode='layout') or '' for page in reader.pages)
        except ProtocolFormatError:
            raise
        except Exception as exc:
            raise ProtocolFormatError('Не удалось прочитать PDF-протокол') from exc
        participants, unresolved = parse_protocol_text(text)
        return participants, unresolved, url

    def contacts(self, identifier):
        data = self.api.get_json('/api/core/api/public/organizations/', {'q': identifier, 'limit': 100})
        rows = data if isinstance(data, list) else data.get('results', [])
        matches = [row for row in rows if str(row.get('iin_bin') or row.get('bin') or '') == identifier]
        if not matches:
            raise SourceError('Участник не найден в публичном реестре по точному БИН/ИИН')
        # An individual's registry entry and their sole-proprietor company can
        # share an IIN. Use the supplier business role, not the person's card.
        suppliers = [row for row in matches if row.get('is_supplier') is True]
        if suppliers:
            matches = suppliers
        registry_ids = {int(row['external_id']) for row in matches
                        if str(row.get('external_id', '')).isdigit() and int(row['external_id']) > 0}
        if len(registry_ids) != 1:
            raise SourceError('Неоднозначное сопоставление участника с публичной карточкой')
        registry_id = registry_ids.pop()
        url = f'https://old.goszakup.gov.kz/ru/registry/show_supplier/{registry_id}'
        result = parse_registry_card(self.read(url), identifier)
        return dict(result, registry_id=registry_id, source_url=url)
