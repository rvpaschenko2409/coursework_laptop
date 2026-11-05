"""Append-only sensitivity experiments bound to verified historical result pairs."""
from decision_core.compatibility import LEGACY_SENSITIVITY
from uuid import uuid5,NAMESPACE_URL
from psycopg.types.json import Jsonb
from decision_store.calculations import digest,plain,assert_close
from web_app.calculation_store import load_pair
from web_app import sensitivity_math as math


def source(data):
    fields=('id','project_id','method','created_by','created_at','project_revision','status','params','core_version')
    return plain({'input':data['snapshot'],'weighting':data['weighting'],
        'weight_set':{key:data['weight_set'][key] for key in ('id','method','alpha','project_revision','created_by')},
        'records':[{key:r[key] for key in fields} for r in data['results'].values()],
        'outputs':{method:data['calculated'][method] for method in ('marcos','vikor')}})


def run(connection,pid,rid,actor,step):
    # Caller checks analyst role/current revision and holds the project lock.
    status=connection.execute('SELECT status FROM projects WHERE id=%s',(pid,)).fetchone()
    if not status or status['status']=='archived':raise ValueError('Нельзя сохранять новый анализ в архивном проекте.')
    data=load_pair(connection,pid,rid)
    if data is None:raise ValueError('Исходный расчёт не найден в этом проекте.')
    canonical=data['results']['marcos']['id']
    snapshot={'schema_version':1,'project':data['snapshot']['project'],'source':source(data),
        'parameters':{'rule':math.RULE,'step_percent':step},
        'producer':{'entrypoint':'web_sensitivity','requested_by':actor,'version':math.version()}}
    output=math.compute(snapshot);fingerprint=digest(snapshot)
    key=uuid5(NAMESPACE_URL,'coursework:laptop:sensitivity:v1:'+fingerprint)
    existing=connection.execute('SELECT id FROM results WHERE project_id=%s AND request_key=%s',(pid,key)).fetchone()
    if existing:
        saved=load(connection,pid,existing['id'])
        if saved['snapshot']!=snapshot:raise ValueError('Прежний анализ не соответствует входам.')
        return {'result_id':existing['id'],'created':False,'source_result_id':canonical,'scenario_count':len(output['scenarios'])}
    params={'entrypoint':'web_sensitivity','source_result_id':canonical,'step_percent':step,'input_fingerprint':fingerprint,'requested_by':actor}
    result_id=connection.execute('''INSERT INTO results(project_id,created_by,request_key,project_revision,status,method,params,input_snapshot,output,core_version,finished_at)
        VALUES(%s,%s,%s,%s,'succeeded','sensitivity',%s,%s,%s,%s,now()) RETURNING id''',
        (pid,actor,key,snapshot['project']['revision'],Jsonb(params),Jsonb(snapshot),Jsonb(output),math.version())).fetchone()['id']
    connection.execute('''INSERT INTO audit_events(project_id,actor_id,action,entity_type,entity_id,details)
        VALUES(%s,%s,'web_sensitivity','result',%s,%s)''',(pid,actor,str(result_id),Jsonb({'source_result_id':canonical,'step_percent':step,'input_fingerprint':fingerprint})))
    return {'result_id':result_id,'created':True,'source_result_id':canonical,'scenario_count':len(output['scenarios'])}


def load(connection,pid,rid):
    row=connection.execute('''SELECT r.*,u.name AS actor_name FROM results r JOIN users u ON u.id=r.created_by
        WHERE r.project_id=%s AND r.id=%s AND r.method='sensitivity' ''',(pid,rid)).fetchone()
    if not row:return None
    snapshot=row['input_snapshot'];fingerprint=digest(snapshot);version=math.version()
    version=snapshot['producer']['version']
    if version not in (math.version(),LEGACY_SENSITIVITY) or row['core_version']!=version:raise ValueError('Для воспроизведения нужна сохранённая версия анализа из Git.')
    source_id=next(r['id'] for r in snapshot['source']['records'] if r['method']=='marcos')
    expected={'entrypoint':'web_sensitivity','source_result_id':source_id,'step_percent':snapshot['parameters']['step_percent'],
        'input_fingerprint':fingerprint,'requested_by':snapshot['producer']['requested_by']}
    key=uuid5(NAMESPACE_URL,'coursework:laptop:sensitivity:v1:'+fingerprint)
    if row['params']!=expected or str(row['request_key'])!=str(key) or row['created_by']!=expected['requested_by'] or row['project_revision']!=snapshot['project']['revision'] or snapshot['project']['id']!=pid or row['status']!='succeeded':raise ValueError('Нарушена целостность сохранённого анализа.')
    output=math.compute(snapshot);assert_close(row['output'],output)
    current=connection.execute('SELECT revision FROM projects WHERE id=%s',(pid,)).fetchone()['revision']
    return {'record':dict(row),'snapshot':snapshot,'output':output,'is_stale':current!=row['project_revision'],'current_revision':current,'verified':True}


def history(connection,pid,page=1):
    rows=connection.execute('''SELECT r.id,r.project_revision,r.created_at,r.params,u.name AS actor_name FROM results r JOIN users u ON u.id=r.created_by
        WHERE r.project_id=%s AND r.method='sensitivity' AND r.status='succeeded' AND r.params->>'entrypoint'='web_sensitivity'
        ORDER BY r.created_at DESC,r.id DESC LIMIT 21 OFFSET %s''',(pid,(page-1)*20)).fetchall()
    return [dict(r) for r in rows[:20]],len(rows)>20
