"""Public taxonomy from Over-shop navigation, never a hand-maintained category map."""
import re
from urllib.parse import urljoin, urlsplit

import httpx
from django.core.cache import cache
from .overshop import BASE, fetch


def category_path(value):
    parsed=urlsplit(urljoin(BASE,value))
    if (parsed.scheme!='https' or parsed.netloc!='over-shop.kz' or parsed.query or parsed.fragment
            or not re.fullmatch(r'/catalog/(?:[a-zA-Z_\d-]+/)+',parsed.path)
            or any(part.isdigit() for part in parsed.path.split('/') if part)):
        return None
    return parsed.path


def parse_categories(soup):
    names={}
    # Read navigation only: product names, advertisements and arbitrary page text
    # must not become category labels supplied to the model.
    for anchor in soup.select('.menu-item a[href], .sections_wrapper a[href]'):
        path=category_path(anchor['href'])
        name=' '.join(anchor.get_text(' ',strip=True).split())
        if path and 1<=len(name)<=150:names.setdefault(path,name)
    if not names or len(names)>1500:raise ValueError('Не удалось прочитать разделы каталога Over-shop.')
    result=[]
    for path,name in sorted(names.items()):
        parents=[names[prefix] for prefix in sorted(names,key=len) if path.startswith(prefix) and path!=prefix]
        result.append({'path':path,'name':name,'label':' / '.join([*parents,name])})
    return result


def load_categories():
    saved=cache.get('overshop-categories-v1')
    if saved is not None:return saved
    with httpx.Client(timeout=15,follow_redirects=False) as client:
        result=parse_categories(fetch(client,BASE+'/catalog/'))
    cache.set('overshop-categories-v1',result,3600)
    return result


def prioritize_urls(urls,paths,limit=18):
    """Prefer verified sections; reserve capacity for the unrestricted search."""
    paths=[p for p in paths if category_path(p)==p][:3]
    urls=list(dict.fromkeys(urls))
    if not paths:return urls[:limit]
    groups=[[u for u in urls if urlsplit(u).path.startswith(p)] for p in paths]
    preferred=[]
    for index in range(max((len(group) for group in groups),default=0)):
        for group in groups:
            if index<len(group) and group[index] not in preferred:preferred.append(group[index])
    fallback=[u for u in urls if u not in preferred]
    reserve=min(4,len(fallback),limit)
    selected=preferred[:limit-reserve]+fallback[:reserve]
    selected.extend(u for u in urls if u not in selected)
    return selected[:limit]
