from pathlib import Path
import httpx,re
from html.parser import HTMLParser
class Inputs(HTMLParser):
 def __init__(self):super().__init__();self.checked={}
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if tag=='input' and a.get('name') in ('status','method') and 'checked' in a:self.checked.setdefault(a['name'],[]).append(a.get('value'))
def main():
 env=dict(l.split('=',1) for l in Path('.env').read_text(encoding='utf-8').splitlines() if l and not l.startswith('#'))
 with httpx.Client(base_url='http://127.0.0.1:8000',timeout=30) as c:
  login=c.get('/login/'); token=re.search('name="csrfmiddlewaretoken" value="([^"]+)"',login.text).group(1)
  r=c.post('/login/',data={'username':env['DJANGO_SUPERUSER_USERNAME'],'password':env['DJANGO_SUPERUSER_PASSWORD'],'csrfmiddlewaretoken':token});assert r.status_code==302
  for path in ['/search/','/search/?profile=6','/search/e30fbcb9-dce5-4e12-9b6d-5c349aae244b/']:
   r=c.get(path);assert r.status_code==200
   parser=Inputs();parser.feed(r.text);assert parser.checked.get('status')==['6'],(path,parser.checked);assert parser.checked.get('method')==['12'],(path,parser.checked)
   print('Defaults OK:',path)
 public=Path('data/tunnel-url.txt').read_text().strip()
 r=httpx.get(public+'/login/',timeout=30);assert r.status_code==200
 print('Tunnel reachable')
if __name__=='__main__':main()
