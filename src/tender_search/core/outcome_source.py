"""Lot-scoped decisions from current public protocols; ambiguous evidence never means defeat."""
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from io import BytesIO
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from pypdf import PdfReader
from tender_search.client import SourceError
from .lead_source import LeadPortal, ProtocolFormatError, compact, parse_protocol_text

@dataclass
class Outcome:
    state: str
    winner_bin: str = ''
    winner_name: str = ''
    protocol_url: str = ''
    fingerprint: str = ''
    participants_complete: bool = False


def normalize_number(value):
    return compact(str(value)).upper().replace('–','-').replace('—','-')


def protocol_text(data):
    if data.lstrip().startswith(b'<'):
        soup=BeautifulSoup(data,'html.parser')
        for item in soup(['script','style']): item.decompose()
        for row in list(soup.select('tr')):
            cells=row.find_all(['th','td'],recursive=False)
            if cells: row.replace_with('\n'+'  '.join(compact(cell.get_text(' ',strip=True)) for cell in cells)+'\n')
        return soup.get_text('\n',strip=False)
    if not data.startswith(b'%PDF-'):
        raise ProtocolFormatError('Формат протокола не поддерживается.')
    try:
        reader=PdfReader(BytesIO(data))
        if reader.is_encrypted or len(reader.pages)>150:
            raise ProtocolFormatError('Протокол зашифрован или содержит более 150 страниц.')
        return '\n'.join(page.extract_text(extraction_mode='layout') or '' for page in reader.pages)
    except ProtocolFormatError: raise
    except Exception as exc:
        raise ProtocolFormatError('Не удалось прочитать текст протокола.') from exc


def parse_lot_result(text, announcement_number, lot_number, company_bin):
    text=text.replace('–','-').replace('—','-')
    number=normalize_number(lot_number)
    expected=normalize_number(announcement_number)
    if not expected or not re.search(r'(?<![\w-])'+re.escape(expected)+r'(?![\w-])',text,re.I):
        raise ProtocolFormatError('Протокол не подтвердил номер объявления.')
    russian=re.search(r'Протокол\s+об\s+итогах',text,re.I)
    if russian: text=text[russian.start():]
    headings=list(re.finditer(r'(?im)^\s*(?:Лот\s*№|№\s*лота|Лоттың\s*№)\s*:?\s*(\d+[\w-]*-+[\w-]+)',text))
    blocks=[text[m.start():headings[i+1].start() if i+1<len(headings) else len(text)]
        for i,m in enumerate(headings) if normalize_number(m.group(1))==number]
    if not blocks:
        raise ProtocolFormatError('В протоколе не найдена секция нужного лота.')
    participants={}; winners=set(); unresolved=0; terminal=set()
    for block in blocks:
        supplier_block=re.sub(r'(?im)^.*Протокол\s+об\s+итогах.*$', '', block)
        people,errors=parse_protocol_text(supplier_block);unresolved+=errors
        for person in people: participants[person.identifier]=person.name
        flat=compact(block)
        for match in re.finditer(r'Определить\s+победителем\s+по\s+лоту\s*№\s*'+re.escape(number)+r'\s*:\s*(\d{12})(?!\d)',flat,re.I):
            winners.add(match.group(1))
        if not winners and len(people)==1 and re.search(r'Решено\s+заключить\s+договор\s+с\s+приглашенным\s+поставщиком',flat,re.I):
            winners.add(people[0].identifier)
        if re.search(r'Признать.{0,100}несостоявш|закупк[аи].{0,60}не\s+состоял',flat,re.I): terminal.add('unsuccessful')
        if re.search(r'Решено\s*:\s*отменить.{0,60}закупк',flat,re.I): terminal.add('cancelled')
    if unresolved or len(winners)>1 or (winners and terminal) or len(terminal)>1:
        raise ProtocolFormatError('Итоги лота неоднозначны; требуется ручная проверка.')
    if terminal: return Outcome(terminal.pop())
    if len(winners)!=1 or not winners.issubset(participants):
        raise ProtocolFormatError('Победитель нужного лота не подтверждён.')
    winner=next(iter(winners))
    state='won' if winner==company_bin else ('lost' if company_bin in participants else 'not_listed')
    counts = set()
    for block in blocks:
        counts.update(int(value) for value in re.findall(
            r'Количество\s+(?:поданных|представленных)\s+заявок\s*[:\-]?\s*(\d+)', compact(block), re.I))
    # Presence proves participation; absence requires a verified full count.
    complete = counts == {len(participants)} and len(participants) > 0
    return Outcome(state,winner,participants[winner],participants_complete=complete)


def select_current_protocol(html, page_url):
    soup=BeautifulSoup(html,'html.parser');tables=[];had_rows=False;candidates=[]
    for heading in soup.find_all(string=re.compile(r'Протокол\s+итогов',re.I)):
        parent=heading.find_parent('table')
        table=parent if parent and parent.select('a[href]') else heading.find_next('table')
        if table is not None and all(table is not value for value in tables): tables.append(table)
    for table in tables:
        for row in table.select('tr'):
            cells=row.find_all('td',recursive=False)
            if not cells: continue
            had_rows=True
            if compact(cells[-1].get_text(' ',strip=True)).casefold()!='действительный': continue
            for link in row.select('a[href]'):
                if 'просмотреть протокол' not in compact(link.get_text()).casefold(): continue
                raw=compact(cells[1].get_text(' ',strip=True)) if len(cells)>1 else ''
                when=None
                for fmt in ('%d.%m.%Y %H:%M:%S','%d.%m.%Y %H:%M','%d.%m.%Y','%Y-%m-%d %H:%M:%S'):
                    try: when=datetime.strptime(raw,fmt);break
                    except ValueError: pass
                if when is None: raise ProtocolFormatError('Дата версии протокола не распознана.')
                candidates.append((when,urljoin(page_url,link['href'])))
    if not tables:
        # Login/JS/error pages must not be mistaken for a published protocol list.
        if not re.search(r'протокол|общие\s+сведения',soup.get_text(' ',strip=True),re.I):
            raise SourceError('Страница публичных итогов недоступна.')
    if not candidates: return None,had_rows
    newest=max(when for when,url in candidates)
    urls={url for when,url in candidates if when==newest}
    if len(urls)!=1: raise ProtocolFormatError('Неоднозначная действующая версия протокола.')
    return (newest.isoformat(),urls.pop()),had_rows


class OutcomePortal(LeadPortal):
    def outcome(self, external_id, announcement_number, lot_number, company_bin):
        page_url=f'https://old.goszakup.gov.kz/ru/announce/index/{int(external_id)}?tab=protocols'
        try: html=self.read(page_url)
        except SourceError:
            page_url=f'https://goszakup.gov.kz/ru/announce/index/{int(external_id)}?tab=protocols'
            html=self.read(page_url)
        selected,had_rows=select_current_protocol(html,page_url)
        if selected is None: return Outcome('review' if had_rows else 'pending')
        _,url=selected
        data=self.read(url,max_bytes=20*1024*1024)
        latest,_=select_current_protocol(self.read(page_url),page_url)
        if latest!=selected: return Outcome('review')
        result=parse_lot_result(protocol_text(data),announcement_number,lot_number,company_bin)
        result.protocol_url=url;result.fingerprint=sha256(data).hexdigest()
        return result
