"""Explain the saved prediction's model and feature snapshot, never an accidental active model."""
import numpy as np
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from sklearn.metrics import mean_absolute_error

from web_app import model_explanations_math as math
from web_app import laptop_predictions
from web_app.alternative_validation import FEATURES
from web_app.model_training_math import validate_records
from web_app.project_store import audit
from web_app.training_data_store import digest


def ready(connection):
    if connection.execute("SELECT to_regclass('web_model_explanations') AS t").fetchone()['t'] is None:
        raise HTTPException(503,'Сначала выполните .\\.venv\\Scripts\\python.exe -m scripts.prepare_model_explanations и перезапустите сервер.')


def predictions(connection,pid):
    return connection.execute('''SELECT p.id,p.value,p.model_id,p.created_at,a.id AS alternative_id,a.code,a.name,
        m.algorithm,m.version,m.is_active FROM predictions p JOIN alternatives a ON a.project_id=p.project_id AND a.id=p.alternative_id
        JOIN ml_models m ON m.project_id=p.project_id AND m.id=p.model_id WHERE p.project_id=%s ORDER BY p.created_at DESC,p.id DESC LIMIT 200''',(pid,)).fetchall()


def history(connection,pid):
    return connection.execute('''SELECT id,model_id,prediction_id,created_at,input_snapshot->'prediction'->>'alternative_code' AS code
        FROM web_model_explanations WHERE project_id=%s ORDER BY id DESC LIMIT 50''',(pid,)).fetchall()


def result(connection,pid,eid):
    row=connection.execute('SELECT * FROM web_model_explanations WHERE project_id=%s AND id=%s',(pid,eid)).fetchone()
    if not row:raise HTTPException(404,'Объяснение не найдено в этом проекте.')
    return dict(row)


def prepare(connection,pid,prediction_id):
    pred=connection.execute('''SELECT p.*,a.code AS alternative_code,a.name AS alternative_name FROM predictions p
        JOIN alternatives a ON a.project_id=p.project_id AND a.id=p.alternative_id WHERE p.project_id=%s AND p.id=%s''',(pid,prediction_id)).fetchone()
    if not pred:raise HTTPException(404,'Сохранённый прогноз не найден в проекте.')
    model=connection.execute('SELECT * FROM ml_models WHERE project_id=%s AND id=%s',(pid,pred['model_id'])).fetchone()
    if not model or model['feature_names']!=FEATURES:raise HTTPException(409,'Версия модели или состав признаков недоступны.')
    rows=[dict(r) for r in connection.execute('''SELECT row_number,configuration_id,split::text AS split,data FROM dataset_rows
        WHERE dataset_id=%s AND layer='processed' ORDER BY row_number''',(model['dataset_id'],)).fetchall()]
    if not rows:raise HTTPException(409,'Нет сохранённого обработанного набора с обучением и тестом.')
    try:validated,_,derived_groups=validate_records(rows)
    except ValueError as exc:raise HTTPException(409,str(exc)) from None
    normalized=[{**old,'data':new['data']} for old,new in zip(rows,validated)]
    train=[r for r in normalized if r['split']=='train'];test=[r for r in normalized if r['split']=='test']
    if len(train)+len(test)!=len(rows) or not train or not test:raise HTTPException(409,'Разделение данных модели не поддерживается.')
    tg={r['configuration_id'] for r in train};vg={r['configuration_id'] for r in test}
    derived_train={int(group) for row,group in zip(rows,derived_groups) if row['split']=='train'}
    derived_test={int(group) for row,group in zip(rows,derived_groups) if row['split']=='test'}
    if tg&vg or derived_train&derived_test:raise HTTPException(409,'В наборе модели пересекаются конфигурации обучения и теста.')
    manifest=model['split_manifest']
    expected_train=manifest.get('train_groups',manifest.get('train_configuration_ids',[]))
    expected_test=manifest.get('test_groups',manifest.get('test_configuration_ids',[]))
    if tg!=set(map(str,expected_train)) or vg!=set(map(str,expected_test)):
        raise HTTPException(409,'Разделение в БД не совпало со снимком модели.')
    if 'assignments' in manifest:
        saved={a['row_number']:(a['configuration_id'],a['split']) for a in manifest['assignments']}
        if saved!={r['row_number']:(r['configuration_id'],r['split']) for r in rows}:raise HTTPException(409,'Назначения строк отличаются от сохранённого разделения.')
    elif 'test_sample_ids' in manifest:
        if set(map(int,manifest['test_sample_ids']))!={int(r['data']['sample_id']) for r in rows if r['split']=='test'}:
            raise HTTPException(409,'Состав тестовых наблюдений не совпал со снимком модели.')
    try:pipeline=laptop_predictions.load_pipeline(str(laptop_predictions.ROOT),model['artifact_path'],model['artifact_sha256'],model['algorithm'])
    except ValueError as exc:raise HTTPException(409,str(exc)) from None
    target=np.array([r['data']['performance_score'] for r in test])
    mae=float(mean_absolute_error(target,pipeline.predict(math.frame(test))))
    if 'test_mae' not in model['metrics'] or not np.isclose(mae,model['metrics']['test_mae'],atol=1e-6,rtol=1e-8):
        raise HTTPException(409,'Тестовая MAE модели не воспроизводится по сохранённому набору.')
    source=connection.execute('SELECT id,name,origin,sha256 FROM datasets WHERE project_id=%s AND id=%s',(pid,model['dataset_id'])).fetchone()
    context={'model_id':model['id'],'artifact_sha256':model['artifact_sha256'],'algorithm':model['algorithm'],
        'dataset_id':model['dataset_id'],'dataset_sha256':source['sha256'],'data_sha256':digest(rows),
        'split_sha256':digest(manifest),'producer':math.producer()}
    context_hash=digest(context)
    snapshot={'context':context,'dataset':dict(source),'model':{k:model[k] for k in ('id','algorithm','version','artifact_sha256')},
        'prediction':{k:pred[k] for k in ('id','value','alternative_id','alternative_code','alternative_name','features_snapshot')},
        'full_test_mae':mae,'train_rows':len(train),'test_rows':len(test),'group_overlap':0}
    fingerprint=digest({'context':context,'prediction':snapshot['prediction']})
    return {'pipeline':pipeline,'model':model,'prediction':pred,'train':train,'test':test,
            'context_hash':context_hash,'fingerprint':fingerprint,'snapshot':snapshot}


def calculate(connection,pid,actor,prediction_id):
    connection.execute('SELECT id FROM projects WHERE id=%s FOR KEY SHARE',(pid,))
    data=prepare(connection,pid,prediction_id)
    existing=connection.execute('SELECT id FROM web_model_explanations WHERE project_id=%s AND fingerprint=%s',(pid,data['fingerprint'])).fetchone()
    if existing:return existing['id'],False
    if not connection.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(current_schema()||':model-explanations',0)) AS ok").fetchone()['ok']:
        raise HTTPException(409,'Уже рассчитывается объяснение. Повторите запрос после его завершения.')
    old=connection.execute('SELECT output FROM web_model_explanations WHERE project_id=%s AND context_hash=%s LIMIT 1',(pid,data['context_hash'])).fetchone()
    try:
        importance=old['output']['importance'] if old else math.importance(data['pipeline'],data['test'])
        local=math.local(data['pipeline'],data['model']['algorithm'],data['prediction']['features_snapshot'],data['train'],data['prediction']['value'])
    except ValueError as exc:raise HTTPException(409,str(exc)) from None
    if not connection.execute('SELECT 1 FROM project_members m JOIN users u ON u.id=m.user_id WHERE m.project_id=%s AND m.user_id=%s AND u.is_active',(pid,actor)).fetchone():
        raise HTTPException(403,'Доступ к проекту изменён во время расчёта.')
    output={'importance':importance,'local':local}
    row=connection.execute('''INSERT INTO web_model_explanations(project_id,model_id,prediction_id,created_by,fingerprint,context_hash,input_snapshot,output)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(project_id,fingerprint) DO NOTHING RETURNING id''',
        (pid,data['model']['id'],prediction_id,actor,data['fingerprint'],data['context_hash'],Jsonb(data['snapshot']),Jsonb(output))).fetchone()
    if not row:return connection.execute('SELECT id FROM web_model_explanations WHERE project_id=%s AND fingerprint=%s',(pid,data['fingerprint'])).fetchone()['id'],False
    audit(connection,pid,actor,'web_model_explanation',{'explanation_id':row['id'],'model_id':data['model']['id'],
        'prediction_id':prediction_id,'fingerprint':data['fingerprint'],'additivity_error':local['additivity_error']})
    return row['id'],True
