"""Bounded public catalogue reads. No credentials, purchases or inferred specifications."""
import re
from decimal import Decimal,InvalidOperation
from urllib.parse import urljoin,urlsplit
from concurrent.futures import ThreadPoolExecutor
import httpx
from bs4 import BeautifulSoup
from .lot_pricing import calculate_price

BASE='https://over-shop.kz'


def public_url(value):
    url=urljoin(BASE,value)
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.netloc!='over-shop.kz' or not re.fullmatch(r'/catalog/(?:[\w-]+/)+\d+/',parsed.path):
        return None
    return url.split('?')[0]


def fetch(client,url,**kwargs):
    # Redirects are deliberately not followed to another host or login page.
    with client.stream('GET',url,**kwargs) as response:
        response.raise_for_status()
        data=bytearray()
        for part in response.iter_bytes():
            data.extend(part)
            if len(data)>3*1024*1024:raise ValueError('Страница каталога слишком большая.')
    return BeautifulSoup(data.decode('utf8',errors='replace'),'html.parser')


def parse_product(soup,url):
    heading=soup.select_one('h1')
    if not heading:raise ValueError('Не найдено название товара.')
    prices={}
    for label in soup.select('.price_group .price_name'):
        price=label.parent.select_one('.price[data-currency="KZT"][data-value]')
        kind='online' if 'Онлайн' in label.get_text() else 'club' if 'Клубная' in label.get_text() else None
        if not price or not kind:continue
        try:
            amount=Decimal(price['data-value'])
            if amount.is_finite() and 0<amount<Decimal('1000000000000'):prices.setdefault(kind,str(amount))
        except InvalidOperation:pass
    attributes={}
    for row in soup.select('table.props_list tr'):
        cells=row.select('td')
        if len(cells)==2:
            key,value=(c.get_text(' ',strip=True) for c in cells)
            if key and value:attributes[key]=value
    stock=soup.select_one('.item-stock')
    stock_text=stock.get_text(' ',strip=True) if stock else ''
    found=re.search(r'На складах\s*:\s*(\d+)',stock_text)
    from .catalog_categories import category_path
    directory=urlsplit(url).path.rsplit('/',2)[0]+'/'
    crumbs={}
    for anchor in soup.select('.breadcrumbs a[href],.breadcrumb a[href],[itemprop=itemListElement] a[href]'):
        path=category_path(anchor['href'])
        if path and directory.startswith(path):crumbs.setdefault(path,anchor.get('title') or anchor.get_text(' ',strip=True))
    return {'name':heading.get_text(' ',strip=True),'url':url,'prices':prices,
        'category_path':directory,'category_label':' / '.join(crumbs[p] for p in sorted(crumbs,key=len)),
        'attributes':attributes,'stock':int(found[1]) if found else None,'stock_text':stock_text}


def number(value):
    match=re.search(r'\d+(?:[.,]\d+)?',value)
    return Decimal(match[0].replace(',','.')) if match else None


# Explicitly supported clauses; everything else remains visible for manual review.
RULES=[(r'диагонал',r'диагонал'),(r'яркост',r'яркост'),(r'врем[яи]\s+отклик',r'время отклика'),
    (r'частот[аыу].{0,45}(?:обновлен|кадр|разрешени)',r'частота обновления'),
    (r'оперативн.{0,15}памят',r'(?:оперативн|объем памяти)'),
    (r'(?:ssd|твердотельн)',r'(?:ssd|накопител)')]


def unit(value):
    for pattern,name in [(r'гб|gb','gb'),(r'тб|tb','tb'),(r'мб|mb','mb'),
                         (r'дюйм|inch|["″]','inch'),(r'кд|cd','brightness'),
                         (r'мс|ms','ms'),(r'гц|hz','hz')]:
        if re.search(pattern,value.casefold()):return name
    return None


def requirement_lines(text):
    """Remove the generated PDF's left column and rejoin wrapped requirement cells."""
    start=re.search(r'Описание\s+и\s+требуемые',text)
    if not start:return split_requirement_clauses(text.replace('\t',' '))
    text=text[start.start():]
    labels=('Описание и требуемые','функциональные,','технические, качественные и',
            'эксплуатационные','характеристики закупаемых','товаров:')
    lines=[]
    for line in text.splitlines():
        line=line.replace('\t',' ')
        parts=re.split(r' {2,}',line.strip())
        if parts and ' '.join(parts[0].split()) in labels:parts=parts[1:]
        lines.append(' '.join(parts))
    body=re.sub(r'\s+',' ',' '.join(lines)).strip()
    return split_requirement_clauses(body)


def split_requirement_clauses(text):
    # Generated specifications often have several attributes in one table cell.
    # A decimal point is not a sentence boundary; "16:9. Наличие" is.
    text=re.sub(r'\.(?=\s+[А-ЯЁA-Z])|;', '\n', text)
    labels=r'(?:тип\s+(?:матрицы|поверхности экрана)|диагональ\s+экрана|соотношение\s+сторон|(?:максимальное\s+)?разрешение\b|частота\s+(?:обновления|при максимальном разрешении)|яркость\b|углы\s+(?:обзора|наклона)|(?:динамическая\s+)?контрастность\b|время\s+отклика|покрытие\s+экрана|входы\s*:|интерфейсы\s*:|интерфейс\s+подключения|кабели\s+в\s+комплекте|кабель\s+HDMI|цвет(?:\s+корпуса|\s*,?\s*используемый)|отображаемые\s+цвета|потребляемая\s+мощность|стандарт\s+крепления)'
    text=re.sub(r'(?<!максимальное)(?<!динамическая)(?<=\S)[ \t]+(?='+labels+r')','\n',text,flags=re.I)
    # A trailing quote after a number is the inch unit, not decoration.
    return '\n'.join(line.strip(' \t\n.,').lstrip('"') for line in text.splitlines() if line.strip(' \t\n\".,'))


def model_key(text):
    return text.upper().translate(str.maketrans('АВСЕНКМОРТХУ','ABCEHKMOPTXY'))


def required_product_model(text):
    explicit=re.search(r'Фирма\s+модель\s+([a-z0-9-]+(?:[ \t]+[a-z0-9-]+){0,2})',text,re.I)
    if explicit:return explicit[1].strip()
    compact=re.findall(r'\b\d{2}[A-Za-zА-Яа-я]\d[A-Za-zА-Яа-я\d]{3,}(?:/\d+)?\b',text)
    return model_key(compact[0]) if len(set(compact))==1 else ''


def catalogue_queries(query,text,model):
    if model:return [model]
    if query.casefold().startswith('монитор'):
        for line in requirement_lines(text).splitlines():
            if 'диагонал' not in line.casefold() or unit(line)!='inch':continue
            values=re.findall(r'\d+(?:[.,]\d+)?',line)
            if len(values)!=1 or re.search(r'не более|до\s|более|ниже',line,re.I):continue
            size=Decimal(values[0].replace(',','.'))
            if 15<=size<=55:
                return [query+' '+format(size,'f'),query]
    return [query]


def compare(text,attributes):
    checks=[];unverified=[]
    for line in dict.fromkeys(s.strip() for s in requirement_lines(text).splitlines() if s.strip()):
        if len(line)<8:continue
        low=line.casefold()
        matched=False
        if low.startswith(('цвет,','цвет корпуса')):
            color=re.fullmatch(r'цвет(?:\s+корпуса|,\s*используемый в оформлении)\s*[:–—-]?\s*(черн[ыё]й|белый|серый|серебристый)',low)
            values=[v for k,v in attributes.items() if k.casefold()=='цвет корпуса']
            if color and len(values)==1:
                ok=values[0].casefold().replace('ё','е')==color[1].replace('ё','е')
                checks.append({'requirement':line[:600],'actual':values[0],'state':'pass' if ok else 'fail'})
                continue
        if low.startswith('стандарт крепления'):
            mount=re.fullmatch(r'стандарт крепления\s*(?:vesa)?\s*[:–—-]?\s*(\d+)\s*[xх×]\s*(\d+)\s*мм',low)
            values=[v for k,v in attributes.items() if 'стандарт настенного крепления' in k.casefold()]
            if mount and len(values)==1:
                actual=re.fullmatch(r'(\d+)\s*[xх×]\s*(\d+)\s*мм',values[0].casefold())
                if actual:
                    checks.append({'requirement':line[:600],'actual':values[0],'state':'pass' if actual.groups()==mount.groups() else 'fail'})
                    continue
        ports=re.fullmatch(r'(?:интерфейс подключения|интерфейсы|входы)\s*[:–—-]?\s*((?:(?:vga|hdmi|displayport|display port)\s*[, ]*)+)',low)
        if ports:
            names=re.findall(r'vga|hdmi|display\s*port',ports[1]);values=[]
            for name in names:
                candidates=[v for k,v in attributes.items() if re.search(r'вход|разъем',k.casefold()) and name.replace(' ','') in k.casefold().replace(' ','')]
                if len(candidates)!=1 or candidates[0].casefold() not in ('есть','нет'):break
                values.append(candidates[0])
            else:
                checks.append({'requirement':line[:600],'actual':', '.join(n.upper()+': '+v for n,v in zip(names,values)),'state':'pass' if all(v.casefold()=='есть' for v in values) else 'fail'})
                continue
        panel=re.findall(r'\b(?:ips|va|tn|oled)\b',low)
        matrix=[v for k,v in attributes.items() if re.search(r'(?:тип|технология).*матриц',k.casefold())]
        if len(panel)==1 and len(matrix)==1 and re.fullmatch(r'(?:тип\s+)?матриц[аы]\s*[:—-]?\s*'+panel[0]+r'[.\s]*',low):
            actual_panels=re.findall(r'\b(?:ips|va|tn|oled)\b',matrix[0].casefold())
            if len(actual_panels)==1:
                checks.append({'requirement':line[:600],'actual':matrix[0],'state':'pass' if panel[0]==actual_panels[0] else 'fail'})
                continue
        resolution=re.findall(r'\b(\d{3,5})\s*[xх×]\s*(\d{3,5})\b',low)
        displays=[v for k,v in attributes.items() if 'разрешение' in k.casefold()]
        if len(resolution)==1 and len(displays)==1 and re.fullmatch(r'(?:максимальное\s+)?разрешение(?:\s+экрана)?\s*[:—-]?\s*(?:не менее\s+|не более\s+)?\d{3,5}\s*[xх×]\s*\d{3,5}(?:\s*\(?full\s*hd\)?)?[.\s]*',low):
            actual_resolution=re.findall(r'\b(\d{3,5})\s*[xх×]\s*(\d{3,5})\b',displays[0].casefold())
            if len(actual_resolution)==1:
                actual=tuple(map(int,actual_resolution[0]));expected=tuple(map(int,resolution[0]))
                ok=all(a>=e for a,e in zip(actual,expected)) if 'не менее' in low else all(a<=e for a,e in zip(actual,expected)) if 'не более' in low else actual==expected
                checks.append({'requirement':line[:600],'actual':displays[0],'state':'pass' if ok else 'fail'})
                continue
        if low.startswith(('контрастность','соотношение сторон')):
            attribute='контраст' if low.startswith('контрастность') else 'соотношение сторон'
            values=[v for k,v in attributes.items() if attribute in k.casefold() and 'динамич' not in k.casefold()]
            ratios=re.findall(r'\b(\d+)\s*:\s*(\d+)\b',low)
            if len(values)==1 and len(ratios)==1:
                ratio_value=re.sub(r'(?<=\d)[ \u00a0\u202f]+(?=\d{3}(?:\D|$))','',values[0])
                actual_ratios=re.findall(r'\b(\d+)\s*:\s*(\d+)\b',ratio_value)
                residue=re.sub(r'\b\d+\s*:\s*\d+\b','',low)
                if len(actual_ratios)==1 and ratios[0][1]!='0' and actual_ratios[0][1]!='0' and not re.search(r'\d',residue):
                    expected=Decimal(ratios[0][0])/Decimal(ratios[0][1]);actual=Decimal(actual_ratios[0][0])/Decimal(actual_ratios[0][1])
                    ok=actual>=expected if 'не менее' in low else actual<=expected if 'не более' in low else actual==expected
                    checks.append({'requirement':line[:600],'actual':values[0],'state':'pass' if ok else 'fail'})
                    continue
        for requirement,attribute in RULES:
            if not re.search(requirement,low):continue
            # Units can precede the value: "Яркость, не менее кд/м2 250".
            clean=re.sub(r'(?:кд|cd)\s*/\s*[мm][²2]', ' ', low)
            clean=re.sub(r'дюйм\w*|inch|[\"″]|\b(?:мс|ms|гц|hz|гб|gb|тб|tb|мб|mb)\b',' ',clean)
            minimum=re.search(r'(?:не менее|не ниже|\bот\b)\s*[:,]?\s*(\d+(?:[.,]\d+)?)',clean)
            maximum=re.search(r'(?:не более|не выше|\bдо\b)\s*[:,]?\s*(\d+(?:[.,]\d+)?)',clean)
            comparators=len(re.findall(r'не менее|не ниже|не более|не выше|\bот\b|\bдо\b',clean))
            if comparators>1 or (comparators and not (minimum or maximum)):break
            values=[v for k,v in attributes.items() if re.search(attribute,k.casefold()) and number(v) is not None]
            if len(values)!=1 or number(values[0]) is None:break
            # Compare only explicit equal units and a single requirement number.
            # Multiple dimensions/ranges need manual review.
            if len(re.findall(r'\d+(?:[.,]\d+)?',clean))!=1:break
            actual_unit=unit(values[0])
            if not actual_unit:
                key=next(k for k,v in attributes.items() if re.search(attribute,k.casefold()) and number(v) is not None)
                actual_unit=unit(key)
            if not unit(low) or unit(low)!=actual_unit:break
            bound=Decimal((minimum or maximum)[1].replace(',','.')) if comparators else number(clean)
            actual=number(values[0])
            # A bare value is exact, never silently interpreted as a minimum.
            ok=actual>=bound if minimum else actual<=bound if maximum else actual==bound
            checks.append({'requirement':line[:600],'actual':values[0],'state':'pass' if ok else 'fail'})
            matched=True;break
        if not matched:unverified.append(line[:600])
    return checks,unverified[:40]


def choose_price(product,budget,rate):
    if not budget:return None
    for kind in ('online','club'):
        value=product['prices'].get(kind)
        if not value:continue
        gross=Decimal(value)
        result=calculate_price(gross,rate,budget['quantity'])
        if result['unit_net']<=budget['unit'] and result['total_net']<=budget['total']:
            return {'kind':kind,'unit_gross':str(gross),'unit_net':str(result['unit_net']),
                'total_net':str(result['total_net'])}
    return None


def search_catalogue(queries,*,category_paths=()):
    """Fetch real public cards only. Search terms never become fetch URLs."""
    queries=list(dict.fromkeys(' '.join(q.split())[:120] for q in queries if q.strip()))[:3]
    if not queries:raise ValueError('Не указаны запросы к каталогу.')
    with httpx.Client(timeout=12,follow_redirects=False,headers={'User-Agent':'Tendersearch/0.1 public product selection'}) as client:
        urls=[];completed_searches=0
        for search in queries:
            try:soup=fetch(client,BASE+'/catalog/',params={'q':search,'s':'1'})
            except (httpx.HTTPError,ValueError):continue
            completed_searches+=1
            found=list(dict.fromkeys(url for a in soup.select('.catalog_block .item-title a[href]')
                if (url:=public_url(a['href']))))[:12]
            urls.extend(url for url in found if url not in urls)
        from .catalog_categories import category_path,prioritize_urls
        paths=[p for p in category_paths if category_path(p)==p][:3]
        for path in paths:
            minimum=12 if len(paths)==1 else 4
            if sum(urlsplit(url).path.startswith(path) for url in urls)>=minimum:continue
            try:section=fetch(client,BASE+path)
            except (httpx.HTTPError,ValueError):continue
            completed_searches+=1
            found=list(dict.fromkeys(url for a in section.select('.catalog_block .item-title a[href]')
                if (url:=public_url(a['href'])) and urlsplit(url).path.startswith(path)))[:12]
            urls.extend(url for url in found if url not in urls)
        if not completed_searches:raise ValueError('Поиск в каталоге Over-shop временно недоступен. Повторите позже.')
        urls=prioritize_urls(urls,category_paths)
        def read(url):
            try:return parse_product(fetch(client,url),url)
            except (httpx.HTTPError,ValueError):return None
        with ThreadPoolExecutor(max_workers=6) as pool:products=list(pool.map(read,urls))
    return [p for p in products if p],len(urls)


def select_products(query,text,budget,rate):
    """Legacy rule matcher retained for regression tests; not the AI task entry point."""
    query=' '.join(query.split())[:120]
    if not query:raise ValueError('Не указано название товара для поиска.')
    text=requirement_lines(text)
    required_model=required_product_model(text)
    queries=catalogue_queries(query,text,required_model)
    search_query=queries[0]
    products,scanned=search_catalogue(queries)
    candidates=[]
    for product in products:
        if not product:continue
        first=re.search(r'[\w-]+',query.casefold())
        product_type=bool(first and re.match(re.escape(first[0])+r'\b',product['name'].casefold()))
        if not product_type:continue
        product['checks'],product['unverified']=compare(text,product['attributes'])
        if required_model:
            matches=all(re.search(r'\b'+re.escape(model_key(word))+r'\b',model_key(product['name'])) for word in required_model.split())
            product['checks'].append({'requirement':'Модель: '+required_model,'actual':product['name'],'state':'pass' if matches else 'fail'})
        product['price']=choose_price(product,budget,rate)
        # A search can return accessories mentioning the requested product.
        product['eligible']=bool(product_type and product['price'] and product['checks'] and not any(c['state']=='fail' for c in product['checks'])
            and product['stock'] is not None and product['stock']>=budget['quantity'])
        candidates.append(product)
    candidates.sort(key=lambda p:(not p['eligible'],sum(c['state']=='fail' for c in p['checks']),
        -sum(c['state']=='pass' for c in p['checks']),Decimal(p['price']['unit_gross']) if p['price'] else Decimal('Infinity')))
    return {'query':search_query,'queries':queries,'required_model':required_model,'candidates':candidates[:6],'scanned':scanned,
        'limitation':'Проверена ограниченная выборка публичного каталога (до 18 товаров). Непроверенные требования нужно сверить вручную; доставка и маржа ещё не учтены.'}


def preparation_fields(product):
    """Use explicit catalogue attributes, never infer a factory/country from a brand."""
    from .goszakup_countries import COUNTRY_CHOICES,canonical_country
    attributes=product.get('attributes') or {}
    def value(keys):
        found={str(v).strip() for k,v in attributes.items() if str(k).strip().casefold().rstrip(':') in keys and str(v).strip()}
        return next(iter(found)) if len(found)==1 else ''
    manufacturer=value({'завод-изготовитель','завод изготовитель','изготовитель','производитель'})
    country=canonical_country(value({'страна происхождения','страна производства','страна-производитель','страна изготовитель','страна-изготовитель'}))
    if country not in dict(COUNTRY_CHOICES):country=''
    return {'product_name':str(product.get('name') or '')[:500], 'manufacturer':manufacturer[:500], 'country':country}
