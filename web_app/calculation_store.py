"""Web orchestration around the unchanged mathematical and historical replay core."""
from decimal import Decimal,InvalidOperation
from uuid import uuid5,NAMESPACE_URL
from psycopg.types.json import Jsonb
from decision_store.calculations import (load_snapshot,compute,digest,assert_close,
    replay_result,core_version,validate_v)


def parse_v(value):
    try:
        if not isinstance(value,str) or len(value)>40:raise ValueError()
        decimal=Decimal(value.strip().replace(',','.'))
        if not decimal.is_finite():raise ValueError()
        return validate_v(float(decimal))
    except (InvalidOperation,ValueError,OverflowError):
        raise ValueError('Параметр v должен быть конечным числом от 0 до 1.') from None


def weight_set(connection,project_id,weight_id,snapshot,calculated):
    stored=connection.execute('SELECT * FROM weight_sets WHERE project_id=%s AND id=%s',(project_id,weight_id)).fetchone()
    if not stored or stored['method']!='merec' or stored['project_revision']!=snapshot['project']['revision'] or stored['input_snapshot'].get('input')!=snapshot:
        raise ValueError('Набор весов не соответствует сохранённому снимку.')
    assert_close(stored['input_snapshot'].get('merec'),calculated['merec'])
    rows=connection.execute('''SELECT c.code,w.value FROM weights w JOIN criteria c ON c.project_id=w.project_id AND c.id=w.criterion_id
        WHERE w.project_id=%s AND w.weight_set_id=%s''',(project_id,weight_id)).fetchall()
    expected={r['criterion']:r['weight'] for r in calculated['merec']['weights']}
    if len(rows)!=len(expected) or {r['code'] for r in rows}!=set(expected):raise ValueError('Состав сохранённых весов изменён.')
    for row in rows:assert_close(float(row['value']),expected[row['code']],'weight')
    return dict(stored)


def run_for_user(connection,project_id,actor_id,v):
    """Caller holds the project row lock and owns transaction/authorization checks."""
    snapshot=load_snapshot(connection,project_id,v)
    # The CLI core uses project.owner_id. Web runs identify the actual session user,
    # without changing a byte of the core or impersonating a disabled demo account.
    snapshot['producer'].update(entrypoint='web',requested_by=actor_id)
    calculated=compute(snapshot);fingerprint=digest(snapshot)
    batch=str(uuid5(NAMESPACE_URL,'coursework:laptop:webcalc:v1:'+fingerprint))
    keys={method:uuid5(NAMESPACE_URL,batch+':'+method) for method in ('marcos','vikor')}
    existing=connection.execute('''SELECT * FROM results WHERE project_id=%s AND request_key IN(%s,%s)''',(project_id,keys['marcos'],keys['vikor'])).fetchall()
    if existing:
        if len(existing)!=2 or {r['method'] for r in existing}!={'marcos','vikor'}:raise ValueError('Неполная пара прежних результатов; она не перезаписана.')
        weight_id=existing[0]['params'].get('weight_set_id')
        for row in existing:
            if row['status']!='succeeded' or row['core_version']!=core_version() or row['created_by']!=actor_id or row['input_snapshot']!=snapshot or row['params'].get('weight_set_id')!=weight_id:
                raise ValueError('Прежний результат не соответствует входам.')
            assert_close(row['output'],calculated[row['method']])
        weight_set(connection,project_id,weight_id,snapshot,calculated)
        ids={r['method']:r['id'] for r in existing};created=False
    else:
        weight_id=connection.execute('''INSERT INTO weight_sets(project_id,method,input_snapshot,project_revision,created_by)
            VALUES(%s,'merec',%s,%s,%s) RETURNING id''',(project_id,Jsonb({'input':snapshot,'merec':calculated['merec']}),snapshot['project']['revision'],actor_id)).fetchone()['id']
        criteria={row['code']:row['id'] for row in snapshot['criteria']}
        with connection.cursor() as cursor:
            cursor.executemany('INSERT INTO weights(project_id,weight_set_id,criterion_id,value) VALUES(%s,%s,%s,%s)',
                [(project_id,weight_id,criteria[row['criterion']],row['weight']) for row in calculated['merec']['weights']])
        ids={}
        for method in ('marcos','vikor'):
            params={'weight_method':'merec','weight_set_id':weight_id,'v':v,'input_fingerprint':fingerprint,'batch_key':batch,'entrypoint':'web','requested_by':actor_id}
            ids[method]=connection.execute('''INSERT INTO results(project_id,created_by,request_key,project_revision,status,method,params,input_snapshot,output,core_version,finished_at)
                VALUES(%s,%s,%s,%s,'succeeded',%s,%s,%s,%s,%s,now()) RETURNING id''',
                (project_id,actor_id,keys[method],snapshot['project']['revision'],method,Jsonb(params),Jsonb(snapshot),Jsonb(calculated[method]),core_version())).fetchone()['id']
        connection.execute('''INSERT INTO audit_events(project_id,actor_id,action,entity_type,entity_id,details)
            VALUES(%s,%s,'web_calculate_decision','project',%s,%s)''',(project_id,actor_id,str(project_id),Jsonb({'result_ids':ids,'weight_set_id':weight_id,'input_fingerprint':fingerprint,'v':v})))
        created=True
    return {'project_id':project_id,'created':created,'result_ids':ids,'weight_set_id':weight_id,
        'project_revision':snapshot['project']['revision'],'input_fingerprint':fingerprint,
        'marcos_leader':calculated['marcos']['ranking'][0]['alternative_code'],'vikor_leader':calculated['vikor']['conditions']['leader'],
        'compromise_set':calculated['vikor']['conditions']['compromise_set'],'excluded':calculated['excluded_codes']}


def history(connection,project_id,page=1):
    rows=connection.execute('''SELECT min(r.id) AS result_id,r.params->>'batch_key' AS batch_key,max(r.created_at) AS created_at,
        min(r.project_revision) AS project_revision,min(r.created_by) AS created_by,count(*) AS method_count
        FROM results r WHERE r.project_id=%s AND r.status='succeeded' AND r.method IN('marcos','vikor') AND r.params->>'batch_key' IS NOT NULL
        GROUP BY r.params->>'batch_key' ORDER BY max(r.created_at) DESC,min(r.id) DESC LIMIT 21 OFFSET %s''',(project_id,(page-1)*20)).fetchall()
    items=[]
    for row in rows[:20]:
        actor=connection.execute('SELECT name FROM users WHERE id=%s',(row['created_by'],)).fetchone()
        items.append({**dict(row),'actor_name':actor['name'] if actor else 'Неизвестный пользователь'})
    return items,len(rows)>20


def load_pair(connection,project_id,result_id):
    first=connection.execute('SELECT * FROM results WHERE project_id=%s AND id=%s',(project_id,result_id)).fetchone()
    if not first:return None
    batch=first['params'].get('batch_key')
    if not batch:raise ValueError('Результат не содержит идентификатора пары расчётов.')
    rows=connection.execute('''SELECT r.*,u.name AS actor_name FROM results r JOIN users u ON u.id=r.created_by
        WHERE r.project_id=%s AND r.params->>'batch_key'=%s ORDER BY r.method''',(project_id,batch)).fetchall()
    if len(rows)!=2 or {r['method'] for r in rows}!={'marcos','vikor'}:raise ValueError('Не найдена полная пара MARCOS и VIKOR.')
    snapshot=first['input_snapshot'];calculated=compute(snapshot)
    for row in rows:
        if row['input_snapshot']!=snapshot or row['params'].get('weight_set_id')!=first['params'].get('weight_set_id') or row['created_by']!=first['created_by']:
            raise ValueError('Методы рассчитаны по разным входам или наборам весов.')
        replay_result(connection,row['id'],project_id)
    stored=weight_set(connection,project_id,first['params'].get('weight_set_id'),snapshot,calculated)
    current=connection.execute('SELECT revision FROM projects WHERE id=%s',(project_id,)).fetchone()['revision']
    by_code={row['code']:row for row in snapshot['alternatives']}
    criteria={row['code']:row for row in snapshot['criteria']}
    vikor={row['alternative_code']:row for row in calculated['vikor']['ranking']}
    comparison=[{'code':r['alternative_code'],'name':by_code[r['alternative_code']]['name'],'marcos':r,'vikor':vikor[r['alternative_code']]} for r in calculated['marcos']['ranking']]
    score_map={(s['alternative_id'],s['criterion_id']):s for s in snapshot['scores']}
    matrix=[]
    for code,values in zip(snapshot['matrix']['rows'],snapshot['matrix']['values']):
        a=by_code[code]
        matrix.append({'code':code,'name':a['name'],'cells':[{'value':v,'source':score_map[(a['id'],criteria[c]['id'])]['source'],
            'override':score_map[(a['id'],criteria[c]['id'])]['source']=='manual' and score_map[(a['id'],criteria[c]['id'])]['prediction_id'] is not None}
            for c,v in zip(snapshot['matrix']['columns'],values)]})
    return {'snapshot':snapshot,'calculated':calculated,'results':{r['method']:dict(r) for r in rows},'weight_set':stored,
        'comparison':comparison,'criteria':criteria,'matrix':matrix,'is_stale':current!=first['project_revision'],'current_revision':current,
        'excluded':[by_code[c] for c in calculated['excluded_codes']],'verified':True}
