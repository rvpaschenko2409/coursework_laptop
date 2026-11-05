"""Immutable weight provenance, atomic paired calculations and historical replay."""
from decision_core.compatibility import LEGACY_WEIGHTS
from uuid import uuid5,NAMESPACE_URL
from psycopg.types.json import Jsonb
from decision_store.calculations import load_snapshot,digest,assert_close
from web_app import weight_math as math
from web_app.dematel_store import load as load_dematel


def selection_from_form(connection,pid,form,items):
    method=form.get('method','merec')
    if method not in math.LABELS:raise ValueError('Выберите известный метод весов.')
    base={'csrf','revision','method','v'}
    if method=='merec':
        if set(form)-base:raise ValueError('Для MEREC переданы посторонние поля.')
        return None
    allowed=base|{'reason'};selection={'version':math.VERSION,'method':method}
    source=method
    if method=='combined':
        allowed|={'alpha','subjective_source'};source=form.get('subjective_source','manual')
        if source not in ('manual','dematel'):raise ValueError('Выберите ручной источник или DEMATEL.')
        selection.update(alpha=form.get('alpha',''),subjective_source=source)
        math.alpha_value(selection['alpha'])
    if source=='manual':
        allowed|={f"weight_{c['id']}" for c in items}
        raw={key[7:]:value for key,value in form.items() if key.startswith('weight_')}
        math.manual_weights(raw,items);selection['raw_manual']=raw
    else:
        allowed.add('dematel_id')
        raw=form.get('dematel_id','')
        if not raw.isascii() or not raw.isdecimal() or len(raw)>18 or int(raw)<=0:raise ValueError('Выберите сохранённый расчёт DEMATEL этого проекта.')
        data=load_dematel(connection,pid,int(raw))
        if not data:raise ValueError('Расчёт DEMATEL не найден в этом проекте.')
        if data['is_stale']:raise ValueError('DEMATEL устарел. Сначала пересчитайте его для текущей версии проекта.')
        selection['dematel']={'structure_id':int(raw),'created_by':data['record']['created_by'],
            'created_at':data['record']['created_at'].isoformat(),'snapshot':data['snapshot'],
            'input_fingerprint':data['record']['input']['input_fingerprint'],'output':data['output']}
    if set(form)-allowed:raise ValueError('Неизвестные поля весов или чужие идентификаторы критериев.')
    reason=form.get('reason','').strip()
    if not 3<=len(reason)<=500 or any(ord(c)<32 and c not in '\n\r\t' for c in reason):raise ValueError('Обоснование выбора весов: 3–500 символов.')
    selection['reason']=reason
    return selection


def verify_weight_set(connection,pid,wid,snapshot,calculated):
    row=connection.execute('SELECT * FROM weight_sets WHERE project_id=%s AND id=%s',(pid,wid)).fetchone()
    weighting=calculated['weighting']
    if not row or row['method']!=weighting['method'] or row['project_revision']!=snapshot['project']['revision'] or row['created_by']!=snapshot['producer']['requested_by']:
        raise ValueError('Не совпали метод, автор или версия набора весов.')
    assert_close(float(row['alpha']) if row['alpha'] is not None else None,weighting['alpha'])
    if row['input_snapshot'].get('input')!=snapshot:raise ValueError('Не совпал снимок набора весов.')
    assert_close(row['input_snapshot'].get('weighting'),weighting)
    rows=connection.execute('SELECT criterion_id,value FROM weights WHERE project_id=%s AND weight_set_id=%s',(pid,wid)).fetchall()
    expected={r['criterion_id']:r['weight'] for r in weighting['weights']}
    if len(rows)!=len(expected) or {r['criterion_id'] for r in rows}!=set(expected):raise ValueError('Состав весов изменён.')
    for r in rows:assert_close(float(r['value']),expected[r['criterion_id']],'stored_weight')
    return dict(row)


def run(connection,pid,actor,v,selection):
    # Caller holds the project lock, checks role, revision, CSRF and transaction.
    snapshot=load_snapshot(connection,pid,v)
    snapshot['parameters']['weight_method']=selection['method']
    if selection['method']=='combined':snapshot['parameters']['alpha']=math.alpha_value(selection['alpha'])
    snapshot['weight_selection']=selection
    snapshot['producer'].update(entrypoint='web_weights',requested_by=actor,weight_adapter_version=math.version())
    calculated=math.compute_weighted(snapshot);fingerprint=digest(snapshot)
    batch=str(uuid5(NAMESPACE_URL,'coursework:laptop:weights:v1:'+fingerprint))
    keys={m:uuid5(NAMESPACE_URL,batch+':'+m) for m in ('marcos','vikor')}
    existing=connection.execute('SELECT * FROM results WHERE project_id=%s AND request_key IN(%s,%s)',(pid,keys['marcos'],keys['vikor'])).fetchall()
    if existing:
        data=load_pair(connection,pid,existing)
        if len(existing)!=2 or any(r['input_snapshot']!=snapshot for r in existing):raise ValueError('Неполная или изменённая пара прежних расчётов.')
        ids={r['method']:r['id'] for r in existing};wid=data['weight_set']['id'];created=False
    else:
        weighting=calculated['weighting']
        wid=connection.execute('''INSERT INTO weight_sets(project_id,method,alpha,input_snapshot,project_revision,created_by)
            VALUES(%s,%s,%s,%s,%s,%s) RETURNING id''',(pid,selection['method'],weighting['alpha'],Jsonb({'input':snapshot,'weighting':weighting}),snapshot['project']['revision'],actor)).fetchone()['id']
        with connection.cursor() as cursor:
            cursor.executemany('INSERT INTO weights(project_id,weight_set_id,criterion_id,value) VALUES(%s,%s,%s,%s)',
                [(pid,wid,r['criterion_id'],r['weight']) for r in weighting['weights']])
        ids={}
        for method in ('marcos','vikor'):
            params={'weight_method':selection['method'],'weight_set_id':wid,'alpha':weighting['alpha'],'v':v,'batch_key':batch,
                'input_fingerprint':fingerprint,'entrypoint':'web_weights','requested_by':actor}
            ids[method]=connection.execute('''INSERT INTO results(project_id,created_by,request_key,project_revision,status,method,params,input_snapshot,output,core_version,finished_at)
                VALUES(%s,%s,%s,%s,'succeeded',%s,%s,%s,%s,%s,now()) RETURNING id''',
                (pid,actor,keys[method],snapshot['project']['revision'],method,Jsonb(params),Jsonb(snapshot),Jsonb(calculated[method]),math.version())).fetchone()['id']
        connection.execute('''INSERT INTO audit_events(project_id,actor_id,action,entity_type,entity_id,details)
            VALUES(%s,%s,'web_weighted_calculation','weight_set',%s,%s)''',(pid,actor,str(wid),Jsonb({'result_ids':ids,'method':selection['method'],'alpha':weighting['alpha'],'input_fingerprint':fingerprint})))
        created=True
    return {'project_id':pid,'created':created,'result_ids':ids,'weight_set_id':wid,'weight_method':selection['method'],
        'alpha':calculated['weighting']['alpha'],'project_revision':snapshot['project']['revision'],'input_fingerprint':fingerprint,
        'marcos_leader':calculated['marcos']['ranking'][0]['alternative_code'],'vikor_leader':calculated['vikor']['conditions']['leader'],
        'compromise_set':calculated['vikor']['conditions']['compromise_set'],'excluded':calculated['excluded_codes']}


def load_pair(connection,pid,rows):
    if len(rows)!=2 or {r['method'] for r in rows}!={'marcos','vikor'}:raise ValueError('Не найдена полная пара рейтингов.')
    first=rows[0];snapshot=first['input_snapshot'];version=math.version()
    if snapshot['producer'].get('weight_adapter_version') not in (version,LEGACY_WEIGHTS):raise ValueError('Для воспроизведения нужна сохранённая версия адаптера весов из Git.')
    version=snapshot['producer']['weight_adapter_version']
    calculated=math.compute_weighted(snapshot);fingerprint=digest(snapshot);actor=snapshot['producer']['requested_by']
    batch=str(uuid5(NAMESPACE_URL,'coursework:laptop:weights:v1:'+fingerprint))
    wid=first['params'].get('weight_set_id')
    for r in rows:
        params=r['params'];expected_key=uuid5(NAMESPACE_URL,batch+':'+r['method'])
        if r['input_snapshot']!=snapshot or r['core_version']!=version or r['status']!='succeeded' or r['created_by']!=actor or r['project_revision']!=snapshot['project']['revision'] or str(r['request_key'])!=str(expected_key):raise ValueError('Нарушена целостность исторического расчёта.')
        expected={'weight_method':snapshot['weight_selection']['method'],'weight_set_id':wid,'alpha':calculated['weighting']['alpha'],
            'v':snapshot['parameters']['v'],'batch_key':batch,'input_fingerprint':fingerprint,'entrypoint':'web_weights','requested_by':actor}
        if params!=expected:raise ValueError('Параметры результата не соответствуют снимку весов.')
        assert_close(r['output'],calculated[r['method']])
    stored=verify_weight_set(connection,pid,wid,snapshot,calculated)
    current=connection.execute('SELECT revision FROM projects WHERE id=%s',(pid,)).fetchone()['revision']
    from web_app.calculation_store import presentation
    return presentation(snapshot,calculated,rows,stored,current)
