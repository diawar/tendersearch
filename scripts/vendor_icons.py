from pathlib import Path
import httpx
import xml.etree.ElementTree as ET
root=Path('src/tender_search/core/static/core')
ET.register_namespace('', 'http://www.w3.org/2000/svg')
names=['search','save','rotate-ccw','layout-dashboard','settings','star','pencil','x','list','layout-grid','folder','log-out','chevron-down','sliders-horizontal','send']
symbols=[]
with httpx.Client(timeout=30,follow_redirects=True) as client:
    for name in names:
        response=client.get('https://raw.githubusercontent.com/lucide-icons/lucide/main/icons/'+name+'.svg')
        response.raise_for_status()
        children=''.join(ET.tostring(child,encoding='unicode') for child in ET.fromstring(response.text))
        symbols.append(f'<symbol id="{name}" viewBox="0 0 24 24">{children}</symbol>')
    response=client.get('https://raw.githubusercontent.com/lucide-icons/lucide/main/LICENSE')
    response.raise_for_status()
    (root/'lucide-LICENSE.txt').write_text(response.text,encoding='utf-8')
(root/'icons.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg">'+''.join(symbols)+'</svg>',encoding='utf-8')
print('Saved',len(symbols),'Lucide icons')
