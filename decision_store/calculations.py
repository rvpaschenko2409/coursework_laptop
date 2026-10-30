"""Calculate and replay immutable MEREC/MARCOS/VIKOR results from database snapshots."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import platform
from uuid import NAMESPACE_URL, UUID, uuid5

import numpy as np
import pandas as pd
from psycopg.types.json import Jsonb

from decision_core.criteria import CRITERIA, MINIMIZE
from decision_core.marcos import calculate_marcos
from decision_core.merec import calculate_merec
from decision_core.normalization import normalize_minmax, normalize_ratio
from decision_core.vikor import calculate_vikor

ROOT=Path(__file__).resolve().parents[1]
LOCK_KEY=27001801
SCHEMA_VERSION=1
TOLERANCE=1e-10


def plain(value):
    if isinstance(value,dict):
        return {str(key):plain(item) for key,item in value.items()}
    if isinstance(value,(tuple,list)):
        return [plain(item) for item in value]
    if isinstance(value,datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value,date):
        return value.isoformat()
    if isinstance(value,(Decimal,np.floating)):
        value=float(value)
    if isinstance(value,np.integer):
        return int(value)
    if isinstance(value,np.bool_):
        return bool(value)
    if isinstance(value,UUID):
        return str(value)
    if isinstance(value,float) and not math.isfinite(value):
        raise ValueError('Снимок содержит неконечное значение.')
    return value


def digest(value: dict) -> str:
    encoded=json.dumps(plain(value),ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def core_version() -> str:
    files=['decision_core/criteria.py','decision_core/normalization.py','decision_core/merec.py',
           'decision_core/marcos.py','decision_core/vikor.py','decision_store/calculations.py']
    return digest({relative:hashlib.sha256((ROOT/relative).read_text(encoding='utf-8-sig').encode('utf-8')).hexdigest() for relative in files})


def validate_v(v: float) -> float:
    if isinstance(v,bool) or not math.isfinite(v) or not 0<=v<=1:
        raise ValueError('Параметр v должен быть конечным числом от 0 до 1.')
    return float(v)


def matrix_from_snapshot(snapshot: dict) -> tuple[pd.DataFrame,list[str]]:
    if snapshot.get('schema_version')!=SCHEMA_VERSION:
        raise ValueError('Неизвестная версия формата снимка.')
    validate_v(snapshot['parameters']['v'])
    criteria=snapshot['criteria']
    if len(criteria)!=5 or {row['code'] for row in criteria}!=set(CRITERIA) or len({row['id'] for row in criteria})!=5:
        raise ValueError('Этот этап поддерживает пять критериев исходного варианта.')
    for row in criteria:
        if row['direction']!=('min' if row['code'] in MINIMIZE else 'max'):
            raise ValueError('Направления критериев не соответствуют проверенному расчётному ядру.')
    alternatives=snapshot['alternatives']
    if not 2<=len(alternatives)<=15 or len({row['id'] for row in alternatives})!=len(alternatives) or len({row['code'] for row in alternatives})!=len(alternatives):
        raise ValueError('Нужно от 2 до 15 альтернатив без повторов идентификаторов и кодов.')
    criterion_ids={row['id']:row['code'] for row in criteria}
    alt_ids={row['id']:row for row in alternatives}
    predictions={row['id']:row for row in snapshot['predictions']}
    models={row['id']:row for row in snapshot['models']}
    datasets={row['id']:row for row in snapshot['datasets']}
    values={}
    for score in snapshot['scores']:
        key=(score['alternative_id'],score['criterion_id'])
        value=float(score['value'])
        if key in values or key[0] not in alt_ids or key[1] not in criterion_ids or not math.isfinite(value) or value<=0:
            raise ValueError('Неверное, повторяющееся или неположительное значение таблицы решений.')
        if score['source'] not in ('manual','ml','calc'):
            raise ValueError('Неизвестный источник оценки.')
        prediction_id=score['prediction_id']
        if prediction_id is not None:
            prediction=predictions.get(prediction_id)
            if not prediction or (prediction['alternative_id'],prediction['criterion_id'])!=key:
                raise ValueError('Оценка ссылается на другой прогноз.')
            model=models.get(prediction['model_id'])
            if not model or model['dataset_id'] not in datasets:
                raise ValueError('Не сохранено происхождение прогноза.')
        if score['source']=='ml':
            if prediction_id is None or value!=float(predictions[prediction_id]['value']):
                raise ValueError('Оценка ML не совпадает с исходным прогнозом.')
            prediction=predictions[prediction_id]
            model=models[prediction['model_id']]
            if not model['is_active']:
                raise ValueError('Модель прогноза неактивна. Обновите прогнозы перед расчётом.')
            current_features={name:alt_ids[key[0]]['features'].get(name) for name in model['feature_names']}
            if current_features!=prediction['features_snapshot']:
                raise ValueError('Признаки ноутбука изменены; прогноз устарел.')
        if score['source']=='manual' and prediction_id is not None and not (score['override_reason'] or '').strip():
            raise ValueError('У ручной корректировки прогноза отсутствует причина.')
        values[key]=value
    by_code={row['code']:row['id'] for row in criteria}
    budget=snapshot['project']['budget']
    if budget is not None and (not math.isfinite(budget) or budget<=0):
        raise ValueError('Бюджет должен быть положительным конечным числом.')
    eligible,excluded=[],[]
    for alternative in alternatives:
        price=values.get((alternative['id'],by_code['price_rub']))
        if price is None:
            raise ValueError(f"Неизвестна цена {alternative['code']}: нельзя проверить бюджет.")
        if budget is not None and price>budget:
            excluded.append(alternative['code'])
        else:
            eligible.append(alternative)
    if len(eligible)<2:
        raise ValueError('После фильтра бюджета осталось меньше двух альтернатив.')
    matrix=[]
    for alternative in eligible:
        row=[]
        for code in CRITERIA:
            value=values.get((alternative['id'],by_code[code]))
            if value is None:
                raise ValueError(f"Не заполнена оценка {alternative['code']} / {code}.")
            row.append(value)
        matrix.append(row)
    frame=pd.DataFrame(matrix,index=[row['code'] for row in eligible],columns=list(CRITERIA))
    return frame,excluded


def table(frame: pd.DataFrame) -> dict:
    return plain({'rows':list(frame.index),'columns':list(frame.columns),'values':frame.to_numpy().tolist()})


def ranking(frame: pd.DataFrame) -> list[dict]:
    return plain(frame.rename_axis('alternative_code').reset_index().to_dict(orient='records'))


def compute(snapshot: dict) -> dict:
    """Pure replay: no database, CSV, model loading or training."""
    matrix,excluded=matrix_from_snapshot(snapshot)
    if snapshot.get('matrix') is not None and snapshot['matrix']!=table(matrix):
        raise ValueError('Матрица снимка не соответствует сохранённым исходным оценкам.')
    merec=calculate_merec(matrix)
    weights=merec.set_index('criterion')['weight']
    marcos=calculate_marcos(matrix,weights)
    vikor,conditions=calculate_vikor(matrix,weights,v=snapshot['parameters']['v'])
    merec_normalized=matrix.copy()
    for column in CRITERIA:
        merec_normalized[column]=(matrix[column]/matrix[column].max() if column in MINIMIZE else matrix[column].min()/matrix[column])
    logs=np.abs(np.log(merec_normalized.to_numpy()))
    sum_logs=logs.sum(axis=1,keepdims=True)
    scores=np.log1p(sum_logs[:,0]/len(CRITERIA))
    without=np.log1p((sum_logs-logs)/len(CRITERIA))
    normalized=normalize_ratio(matrix)
    weighted=normalized.mul(weights,axis='columns')
    ideals={column:float(matrix[column].min() if column in MINIMIZE else matrix[column].max()) for column in CRITERIA}
    anti={column:float(matrix[column].max() if column in MINIMIZE else matrix[column].min()) for column in CRITERIA}
    anti_normalized=pd.Series({column:float(matrix[column].min()/matrix[column].max()) for column in CRITERIA})
    distances=1-normalize_minmax(matrix)
    return plain({'merec':{'weights':merec.to_dict(orient='records'),'normalized':table(merec_normalized),
                           'scores':[{'alternative_code':code,'score':float(value)} for code,value in zip(matrix.index,scores)],
                           'scores_without':table(pd.DataFrame(without,index=matrix.index,columns=list(CRITERIA)))},
                  'marcos':{'ranking':ranking(marcos),'normalized':table(normalized),'weighted':table(weighted),
                            'ideal':ideals,'anti_ideal':anti,'S_ideal':float(weights.sum()),
                            'S_anti_ideal':float((anti_normalized*weights).sum())},
                  'vikor':{'ranking':ranking(vikor),'conditions':conditions,'distances':table(distances),
                           'weighted_distances':table(distances.mul(weights,axis='columns'))},
                  'excluded_codes':excluded})


def load_snapshot(connection,project_id: int,v: float) -> dict:
    if isinstance(project_id,bool) or project_id<=0:
        raise ValueError('Неверный идентификатор проекта.')
    project=connection.execute('''SELECT id,owner_id,title,goal,budget,status,revision,settings,catwoe,root_definition FROM projects WHERE id=%s FOR UPDATE''',(project_id,)).fetchone()
    if not project:
        raise ValueError('Проект не найден.')
    if project['status']=='archived':
        raise ValueError('Проект архивирован.')
    criteria=connection.execute('''SELECT id,code,name,unit,direction::text AS direction,source::text AS source,sort_order FROM criteria WHERE project_id=%s ORDER BY sort_order,id''',(project_id,)).fetchall()
    alternatives=connection.execute('''SELECT id,code,name,description,features,sort_order FROM alternatives WHERE project_id=%s ORDER BY sort_order,id''',(project_id,)).fetchall()
    scores=connection.execute('''SELECT id,alternative_id,criterion_id,expert_id,value,source::text AS source,prediction_id,override_reason,updated_at FROM scores WHERE project_id=%s ORDER BY alternative_id,criterion_id''',(project_id,)).fetchall()
    predictions=connection.execute('''SELECT p.* FROM predictions p WHERE p.project_id=%s AND EXISTS(SELECT 1 FROM scores s WHERE s.project_id=p.project_id AND s.prediction_id=p.id) ORDER BY p.id''',(project_id,)).fetchall()
    models=connection.execute('''SELECT m.* FROM ml_models m WHERE m.project_id=%s AND EXISTS(SELECT 1 FROM predictions p JOIN scores s ON s.prediction_id=p.id WHERE p.model_id=m.id AND s.project_id=m.project_id) ORDER BY m.id''',(project_id,)).fetchall()
    datasets=connection.execute('''SELECT d.* FROM datasets d WHERE d.project_id=%s AND EXISTS(SELECT 1 FROM ml_models m JOIN predictions p ON p.model_id=m.id JOIN scores s ON s.prediction_id=p.id WHERE m.dataset_id=d.id AND s.project_id=d.project_id) ORDER BY d.id''',(project_id,)).fetchall()
    snapshot=plain({'schema_version':SCHEMA_VERSION,'project':dict(project),'criteria':[dict(x) for x in criteria],
                    'alternatives':[dict(x) for x in alternatives],'scores':[dict(x) for x in scores],
                    'predictions':[dict(x) for x in predictions],'models':[dict(x) for x in models],
                    'datasets':[dict(x) for x in datasets],'parameters':{'weight_method':'merec','v':validate_v(v)},
                    'producer':{'core_version':core_version(),'python':platform.python_version(),'numpy':np.__version__,'pandas':pd.__version__}})
    matrix,excluded=matrix_from_snapshot(snapshot)
    snapshot['matrix']=table(matrix)
    snapshot['excluded_codes']=excluded
    snapshot['limitations']=['Учебная синтетика; точность на реальных ноутбуках не подтверждена.'] if any(row['origin']=='synthetic' for row in datasets) else []
    return snapshot


def assert_close(actual,expected,path: str='output') -> None:
    if isinstance(expected,dict):
        if not isinstance(actual,dict) or set(actual)!=set(expected):
            raise ValueError(f'Не совпадает структура {path}.')
        for key in expected:
            assert_close(actual[key],expected[key],f'{path}.{key}')
    elif isinstance(expected,list):
        if not isinstance(actual,list) or len(actual)!=len(expected):
            raise ValueError(f'Не совпадает состав {path}.')
        for i,(left,right) in enumerate(zip(actual,expected)):
            assert_close(left,right,f'{path}[{i}]')
    elif isinstance(expected,float):
        if isinstance(actual,bool) or not isinstance(actual,(int,float)) or not math.isfinite(actual) or abs(float(actual)-expected)>TOLERANCE:
            raise ValueError(f'Не совпадает числовое значение {path}.')
    elif actual!=expected:
        raise ValueError(f'Не совпадает {path}.')


def run_calculation(connection,project_id: int=1,v: float=0.5) -> dict:
    """Caller must start a fresh transaction; commit before reporting success."""
    connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
    connection.execute('SELECT pg_advisory_xact_lock(%s)',(LOCK_KEY,))
    version=connection.execute('SELECT version_num FROM alembic_version').fetchone()
    if not version or version['version_num']!='0002':
        raise ValueError('Ожидается миграция 0002 и зарегистрированные прогнозы.')
    snapshot=load_snapshot(connection,project_id,v)
    calculated=compute(snapshot)
    fingerprint=digest(snapshot)
    batch_key=str(uuid5(NAMESPACE_URL,'coursework:laptop:calc:v1:'+fingerprint))
    keys={method:uuid5(NAMESPACE_URL,batch_key+':'+method) for method in ('marcos','vikor')}
    existing=connection.execute('SELECT * FROM results WHERE project_id=%s AND request_key IN (%s,%s) ORDER BY method',(project_id,keys['marcos'],keys['vikor'])).fetchall()
    actor_id=snapshot['project']['owner_id']
    if existing:
        if len(existing)!=2 or {row['method'] for row in existing}!={'marcos','vikor'}:
            raise ValueError('Неполная пара сохранённых результатов; она не перезаписана.')
        weight_set_id=existing[0]['params'].get('weight_set_id')
        for row in existing:
            if row['status']!='succeeded' or row['core_version']!=snapshot['producer']['core_version'] or row['input_snapshot']!=snapshot or row['params'].get('weight_set_id')!=weight_set_id:
                raise ValueError('Сохранённый результат не соответствует входам.')
            assert_close(row['output'],calculated[row['method']])
        stored=connection.execute('SELECT * FROM weight_sets WHERE project_id=%s AND id=%s',(project_id,weight_set_id)).fetchone()
        if not stored or stored['method']!='merec' or stored['project_revision']!=snapshot['project']['revision'] or stored['input_snapshot'].get('input')!=snapshot:
            raise ValueError('Сохранённый набор весов изменён.')
        assert_close(stored['input_snapshot']['merec'],calculated['merec'])
        actual_weights=connection.execute('''SELECT c.code,w.value FROM weights w JOIN criteria c ON c.id=w.criterion_id WHERE w.project_id=%s AND w.weight_set_id=%s''',(project_id,weight_set_id)).fetchall()
        if len(actual_weights)!=5:
            raise ValueError('Неполный набор весов.')
        expected={row['criterion']:row['weight'] for row in calculated['merec']['weights']}
        for row in actual_weights:
            assert_close(float(row['value']),expected[row['code']],'stored weight')
        ids={row['method']:row['id'] for row in existing}
        created=False
    else:
        weight_set_id=connection.execute('''INSERT INTO weight_sets(project_id,method,input_snapshot,project_revision,created_by)
            VALUES(%s,'merec',%s,%s,%s) RETURNING id''',(project_id,Jsonb({'input':snapshot,'merec':calculated['merec']}),snapshot['project']['revision'],actor_id)).fetchone()['id']
        criterion_ids={row['code']:row['id'] for row in snapshot['criteria']}
        with connection.cursor() as cursor:
            cursor.executemany('INSERT INTO weights(project_id,weight_set_id,criterion_id,value) VALUES(%s,%s,%s,%s)',
                [(project_id,weight_set_id,criterion_ids[row['criterion']],row['weight']) for row in calculated['merec']['weights']])
        ids={}
        for method in ('marcos','vikor'):
            params={'weight_method':'merec','weight_set_id':weight_set_id,'v':validate_v(v),'input_fingerprint':fingerprint,'batch_key':batch_key}
            ids[method]=connection.execute('''INSERT INTO results(project_id,created_by,request_key,project_revision,status,method,params,input_snapshot,output,core_version,finished_at)
                VALUES(%s,%s,%s,%s,'succeeded',%s,%s,%s,%s,%s,now()) RETURNING id''',
                (project_id,actor_id,keys[method],snapshot['project']['revision'],method,Jsonb(params),Jsonb(snapshot),Jsonb(calculated[method]),snapshot['producer']['core_version'])).fetchone()['id']
        connection.execute('''INSERT INTO audit_events(project_id,actor_id,action,entity_type,entity_id,details)
            VALUES(%s,%s,'calculate_decision','project',%s,%s)''',(project_id,actor_id,str(project_id),Jsonb({'batch_key':batch_key,'results':ids,'weight_set_id':weight_set_id,'database_role':connection.execute('SELECT current_user AS role').fetchone()['role']})))
        created=True
    return {'project_id':project_id,'project_revision':snapshot['project']['revision'],'created':created,
            'weight_set_id':weight_set_id,'result_ids':ids,'input_fingerprint':fingerprint,
            'marcos_leader':calculated['marcos']['ranking'][0]['alternative_code'],
            'vikor_leader':calculated['vikor']['conditions']['leader'],
            'vikor_compromise':calculated['vikor']['conditions']['compromise_set'],
            'eligible':snapshot['matrix']['rows'],'excluded':calculated['excluded_codes'],'limitations':snapshot['limitations']}


def replay_result(connection,result_id: int,project_id: int=1) -> dict:
    row=connection.execute('''SELECT r.*,p.revision AS current_revision FROM results r JOIN projects p ON p.id=r.project_id WHERE r.id=%s AND r.project_id=%s''',(result_id,project_id)).fetchone()
    if not row or row['status']!='succeeded' or row['method'] not in ('marcos','vikor'):
        raise ValueError('Завершённый результат этого проекта не найден.')
    if row['core_version']!=core_version():
        raise ValueError('Версия ядра изменилась. Для строгого воспроизведения нужна соответствующая версия кода из Git.')
    snapshot=row['input_snapshot']
    if digest(snapshot)!=row['params'].get('input_fingerprint'):
        raise ValueError('Контрольная сумма снимка не совпала.')
    if row['project_revision']!=snapshot['project']['revision']:
        raise ValueError('Ревизия результата не совпадает со снимком.')
    calculated=compute(snapshot)
    assert_close(row['output'],calculated[row['method']])
    return {'result_id':result_id,'project_id':project_id,'method':row['method'],'verified':True,
            'project_revision':row['project_revision'],'current_revision':row['current_revision'],
            'is_stale':row['project_revision']!=row['current_revision'],
            'ranking':[entry['alternative_code'] for entry in calculated[row['method']]['ranking']],
            'compromise_set':calculated['vikor']['conditions']['compromise_set'] if row['method']=='vikor' else None}
