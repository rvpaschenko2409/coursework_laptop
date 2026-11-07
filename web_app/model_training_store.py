"""Durable job identity, dataset snapshots and atomic model registration."""
from datetime import datetime,timezone
import re
import uuid
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from web_app import model_training_math as math
from web_app.alternative_validation import FEATURES,number
from web_app.project_store import audit,insert_row
from web_app.training_data_store import digest


def ready(connection):
    if connection.execute("SELECT to_regclass('web_training_runs') AS t").fetchone()['t'] is None:
        raise HTTPException(503,'Сначала выполните .\\.venv\\Scripts\\python.exe -m scripts.prepare_model_training и перезапустите сервер.')


def sources(connection,pid):
    return connection.execute('''SELECT d.id,d.name,d.origin,d.row_count,d.quality_report,
        count(r.id) FILTER(WHERE r.layer='staging') AS staging_rows,
        count(r.id) FILTER(WHERE r.layer='processed') AS processed_rows
        FROM datasets d LEFT JOIN dataset_rows r ON r.dataset_id=d.id
        WHERE d.project_id=%s GROUP BY d.id ORDER BY d.uploaded_at DESC,d.id DESC''',(pid,)).fetchall()


def listing(connection,pid):
    return connection.execute('''SELECT id,source_dataset_id,created_by,status,params,progress,output,error,cancel_requested,
        created_at,started_at,finished_at FROM web_training_runs WHERE project_id=%s ORDER BY created_at DESC LIMIT 50''',(pid,)).fetchall()


def completed_options(connection, pid):
    return connection.execute("""
        SELECT id,source_dataset_id,created_at,params->>'seed' AS seed
        FROM web_training_runs WHERE project_id=%s AND status='succeeded'
        ORDER BY created_at DESC,id DESC
        """, (pid,)).fetchall()


def comparison(connection, pid, first, second):
    rows = connection.execute("""
        SELECT id,source_dataset_id,created_at,params,status,output
        FROM web_training_runs WHERE project_id=%s AND id IN (%s,%s)
        """, (pid, first, second)).fetchall()
    by_id = {row['id']: row for row in rows}
    if len(by_id) != 2:
        raise HTTPException(404, 'Запуск не найден в этом проекте.')
    left, right = by_id[first], by_id[second]
    if any(run['status'] != 'succeeded' for run in (left, right)):
        raise HTTPException(422, 'Выберите два успешно завершённых запуска.')

    a, b = left['output'], right['output']
    # The processed dataset identity incorporates the input and full split manifest.
    same_split = bool(a.get('dataset_id') and a['dataset_id'] == b.get('dataset_id'))
    a_models, b_models = a['models'], b['models']
    metrics = (
        ('cv_mae', 'MAE CV ↓'),
        ('test_mae', 'MAE тест ↓'),
        ('test_rmse', 'RMSE тест ↓'),
        ('test_r2', 'R² тест ↑'),
    )
    models = []
    for name in sorted(a_models.keys() | b_models.keys()):
        a_model, b_model = a_models.get(name, {}), b_models.get(name, {})
        values = []
        for key, label in metrics:
            old, new = a_model.get(key), b_model.get(key)
            delta = new - old if same_split and old is not None and new is not None else None
            values.append({'label': label, 'first': old, 'second': new, 'delta': delta})
        models.append({'name': name, 'versions': (a_model.get('version'), b_model.get('version')), 'metrics': values})
    return {'runs': (left, right), 'same_split': same_split, 'models': models}


def models(connection,pid):
    return connection.execute('''SELECT m.id,m.name,m.version,m.algorithm,m.dataset_id,m.params,m.metrics,m.environment,
        m.seed,m.is_active,m.trained_at,m.registered_at,d.origin FROM ml_models m JOIN datasets d
        ON d.project_id=m.project_id AND d.id=m.dataset_id WHERE m.project_id=%s ORDER BY m.registered_at DESC,m.id DESC''',(pid,)).fetchall()


def create(connection,pid,actor,dataset_id,seed,key):
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',key):raise HTTPException(422,'Откройте форму обучения заново.')
    seed=number(seed,'Seed разделения и обучения',0,2147483647,True)
    old=connection.execute('SELECT id FROM web_training_runs WHERE project_id=%s AND created_by=%s AND request_key=%s',(pid,actor,key)).fetchone()
    if old:return str(old['id']),False
    source=connection.execute('SELECT * FROM datasets WHERE project_id=%s AND id=%s',(pid,dataset_id)).fetchone()
    if not source:raise HTTPException(404,'Набор данных не найден в проекте.')
    # Serialize enqueue capacity as well as the per-project unique active-job constraint.
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema()||':training-queue',0))")
    if connection.execute("SELECT 1 FROM web_training_runs WHERE project_id=%s AND status IN ('queued','running')",(pid,)).fetchone():
        raise HTTPException(409,'В этом проекте уже есть обучение в очереди или в работе.')
    if connection.execute("SELECT count(*) AS n FROM web_training_runs WHERE status IN ('queued','running')").fetchone()['n']>=4:
        raise HTTPException(409,'Очередь обучения заполнена. Дождитесь завершения текущих задач.')
    layer='staging' if connection.execute("SELECT 1 FROM dataset_rows WHERE dataset_id=%s AND layer='staging' LIMIT 1",(dataset_id,)).fetchone() else 'processed'
    records=[dict(r) for r in connection.execute('SELECT row_number,data FROM dataset_rows WHERE dataset_id=%s AND layer=%s::data_layer ORDER BY row_number',(dataset_id,layer)).fetchall()]
    # Reject malformed or insufficient input before creating a job; repeat validation in the worker.
    try:math.split(records,seed)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    snapshot={'source':{k:source[k] for k in ('id','name','origin','source_url','license','sha256','seed','generator_version','generation_params')},
              'source_layer':layer,'records':records,'sha256':digest(records),'version':math.VERSION}
    rid=str(uuid.uuid4())
    connection.execute('''INSERT INTO web_training_runs(id,project_id,source_dataset_id,created_by,request_key,params,input_snapshot)
        VALUES(%s,%s,%s,%s,%s,%s,%s)''',(rid,pid,dataset_id,actor,key,Jsonb({'seed':seed,'version':math.VERSION}),Jsonb(snapshot)))
    audit(connection,pid,actor,'web_model_training_queued',{'run_id':rid,'dataset_id':dataset_id,'input_sha256':snapshot['sha256'],'seed':seed})
    return rid,True


def permitted(connection,pid,actor):
    return connection.execute("SELECT 1 FROM project_members m JOIN users u ON u.id=m.user_id WHERE m.project_id=%s AND m.user_id=%s AND m.role='analyst' AND u.is_active",(pid,actor)).fetchone() is not None


def persist(connection,run,result,artifacts):
    pid=run['project_id'];actor=run['created_by'];rid=str(run['id'])
    if not connection.execute('SELECT id FROM projects WHERE id=%s FOR UPDATE',(pid,)).fetchone():raise ValueError('Проект удалён.')
    if not permitted(connection,pid,actor):raise ValueError('Доступ аналитика изменён во время обучения.')
    state=connection.execute('SELECT status,cancel_requested FROM web_training_runs WHERE id=%s FOR UPDATE',(rid,)).fetchone()
    if not state or state['status']!='running' or state['cancel_requested']:raise InterruptedError('Обучение отменено.')
    source=run['input_snapshot']['source'];manifest=result['manifest']
    identity=digest({'source_id':source['id'],'input':run['input_snapshot']['sha256'],'split':manifest,'version':math.VERSION})
    existing=connection.execute('SELECT id FROM datasets WHERE project_id=%s AND sha256=%s',(pid,identity)).fetchone()
    if existing:did=existing['id']
    else:
        did=insert_row(connection,'datasets',dict(project_id=pid,parent_dataset_id=source['id'],
            name=(source['name'][:160]+' · обучение seed '+str(run['params']['seed']))[:200],origin=source['origin'],
            source_url=source['source_url'],license=source['license'],sha256=identity,seed=source['seed'],generator_version=source['generator_version'],
            generation_params={**source['generation_params'],'processing_version':math.VERSION,'source_input_sha256':run['input_snapshot']['sha256'],
                               'split_assigned':True,'training_seed':run['params']['seed']},
            quality_report={'observations':len(result['rows']),'train_rows':manifest['train_rows'],'test_rows':manifest['test_rows'],
                            'train_groups':len(manifest['train_groups']),'test_groups':len(manifest['test_groups']),
                            'split_overlap':0,'origin':source['origin'],'real_world_accuracy_established':False},
            row_count=len(result['rows']),column_count=8,uploaded_by=actor))
        groups=[{'configuration_id':str(g),'split':split} for split in ('train','test') for g in manifest[split+'_groups']]
        connection.execute('''INSERT INTO dataset_groups(dataset_id,configuration_id,split)
            SELECT %s,x.configuration_id,x.split::data_split FROM jsonb_to_recordset(%s) AS x(configuration_id text,split text)''',(did,Jsonb(groups)))
        rows=[]
        for row,assignment in zip(result['rows'],manifest['assignments']):
            rows.append({**assignment,'data':{**row['data'],'configuration_id':assignment['configuration_id']},
                'provenance':{'parent_dataset_id':source['id'],'parent_layer':run['input_snapshot']['source_layer'],
                              'parent_row_number':row['row_number'],'processing_version':math.VERSION,'input_sha256':run['input_snapshot']['sha256']}})
        for start in range(0,len(rows),500):
            connection.execute('''INSERT INTO dataset_rows(dataset_id,layer,row_number,configuration_id,split,data,provenance)
                SELECT %s,'processed',x.row_number,x.configuration_id,x.split::data_split,x.data,x.provenance
                FROM jsonb_to_recordset(%s) AS x(row_number integer,configuration_id text,split text,data jsonb,provenance jsonb)''',(did,Jsonb(rows[start:start+500])))
    ids={};summary={}
    for name,model in result['models'].items():
        version=connection.execute('SELECT coalesce(max(version),0)+1 AS v FROM ml_models WHERE project_id=%s AND name=%s',(pid,name)).fetchone()['v']
        ids[name]=insert_row(connection,'ml_models',dict(project_id=pid,dataset_id=did,name=name,version=version,algorithm=name,
            feature_names=FEATURES,target_name='performance_score',params=model['params'],metrics=model['metrics'],
            environment={**result['environment'],'run_id':rid,'cv_candidates':model['candidates']},split_manifest=manifest,seed=run['params']['seed'],
            artifact_path=artifacts[name]['path'],artifact_sha256=artifacts[name]['sha256'],is_active=False,trained_at=datetime.now(timezone.utc),created_by=actor))
        summary[name]={'model_id':ids[name],'version':version,**model['metrics']}
    output={'models':summary,'recommended':result['selected'],'recommended_model_id':ids[result['selected']],
            'selected_by':'minimum group CV MAE; test scores not used','dataset_id':did,
            'train_rows':manifest['train_rows'],'test_rows':manifest['test_rows'],'train_groups':len(manifest['train_groups']),
            'test_groups':len(manifest['test_groups']),'group_overlap':0,'elapsed_seconds':result['elapsed_seconds'],
            'origin':source['origin'],'input_sha256':result['input_sha256']}
    connection.execute("UPDATE web_training_runs SET status='succeeded',progress='Обучение завершено',output=%s,finished_at=now() WHERE id=%s",(Jsonb(output),rid))
    audit(connection,pid,actor,'web_model_training_succeeded',{'run_id':rid,**output})
    return output
