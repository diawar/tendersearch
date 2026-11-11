"""Presentation of confirmed draft preparation progress; never changes workflow."""
PHASES=[('data','Данные товара'),('portal','Открытие Госзакупа'),('account','Реквизиты'),('lot','Добавление лота'),('description','Описание товара'),('complete','Черновик готов')]
INTENTS={'open_form':'portal','fill_account':'account','save_account':'account','add_lot':'lot','advance_lots':'lot','open_description':'description','edit_description':'description','fill_description':'description','save_description':'description'}


def snapshot_phase(snapshot,previous=''):
    if snapshot.get('status')=='complete':return 'complete'
    phase=snapshot.get('phase')
    if phase in dict(PHASES) and phase!='complete':return phase
    if snapshot.get('intent') in INTENTS:return INTENTS[snapshot['intent']]
    if snapshot.get('lotConfirmed') is True:return 'description'
    if snapshot.get('number'):return 'lot'
    message=str(snapshot.get('message','')).casefold()
    if 'реквизит' in message:return 'account'
    if 'описани' in message:return 'description'
    return previous or 'portal'


def steps_for(item):
    preparation=getattr(item.lot,'preparation',None)
    prepared=item.availability['input_ready'] if hasattr(item,'availability') else bool(preparation and preparation.data.get('product_name') and preparation.data.get('manufacturer'))
    phase='complete' if item.state=='complete' else 'data' if not prepared and item.state not in ('running','waiting') else item.progress_phase or ('portal' if prepared else 'data')
    current=next((i for i,(key,_) in enumerate(PHASES) if key==phase),0)
    result=[]
    for i,(key,label) in enumerate(PHASES):
        status='done' if item.state=='complete' or i<current else 'pending'
        if i==current and item.state!='complete':
            status='error' if item.state=='blocked' else 'waiting' if item.state in ('queued','ready','waiting','paused','cancelled') else 'active'
        result.append({'label':label,'key':key,'status':status,'number':i+1,'current':i==current and item.state!='complete'})
    return result
