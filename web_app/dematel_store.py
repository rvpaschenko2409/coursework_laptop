"""Individual expert matrices and reproducible DEMATEL snapshots."""
from pathlib import Path
from decimal import Decimal,InvalidOperation
import hashlib
import numpy as np
import pandas as pd
from psycopg.types.json import Jsonb
from decision_core.dematel import calculate_dematel
from decision_store.calculations import digest,plain,table,assert_close
from web_app.alternative_validation import InvalidForm

ROOT=Path(__file__).resolve().parents[1]
CATWOE={'C':('Клиенты','Кто получает результат или испытывает последствия?'),
 'A':('Участники','Кто выполняет преобразование?'),'T':('Преобразование','Что и во что преобразует система?'),
 'W':('Мировоззрение','Почему такое преобразование имеет смысл?'),'O':('Владелец','Кто может разрешить или остановить работу?'),
 'E':('Ограничения среды','Какие внешние ограничения нужно учитывать?')}


def core_hash():return hashlib.sha256((ROOT/'decision_core/dematel.py').read_text(encoding='utf-8-sig').encode()).hexdigest()


def criteria(connection,pid):
    rows=[dict(r) for r in connection.execute('SELECT id,code,name,unit,direction::text AS direction FROM criteria WHERE project_id=%s ORDER BY sort_order,id',(pid,)).fetchall()]
    archived=connection.execute('SELECT settings FROM projects WHERE id=%s',(pid,)).fetchone()['settings'].get('archived_criteria',[])
    rows=[r for r in rows if r['id'] not in archived]
    if not 2<=len(rows)<=10:raise ValueError('DEMATEL требует от 2 до 10 критериев.')
    return rows


def matrix_form(form,items):
    expected={f'cell_{i["id"]}_{j["id"]}' for i in items for j in items}
    if set(form)-expected-{'csrf','revision','reason'}:raise InvalidForm({'matrix':'В форме есть неизвестные поля или чужие критерии.'})
    errors={};matrix=[]
    for i in items:
        row=[]
        for j in items:
            key=f'cell_{i["id"]}_{j["id"]}';raw=form.get(key,'')
            if raw not in ('0','1','2','3','4') or (i['id']==j['id'] and raw!='0'):errors[key]='Нужно целое 0–4; на диагонали только 0.'
            row.append(int(raw) if raw in ('0','1','2','3','4') else 0)
        matrix.append(row)
    reason=form.get('reason','').strip()
    if not 3<=len(reason)<=500 or any(ord(c)<32 and c not in '\n\r\t' for c in reason):errors['reason']='Укажите причину сохранения длиной 3–500 символов.'
    if errors:raise InvalidForm(errors)
    return matrix,reason


def save_matrix(connection,pid,actor,items,matrix,reason):
    old=[dict(r) for r in connection.execute('SELECT criterion_i,criterion_j,value FROM comparisons WHERE project_id=%s AND expert_id=%s ORDER BY criterion_i,criterion_j',(pid,actor)).fetchall()]
    old=[r for r in old if r['criterion_i'] in {c['id'] for c in items} and r['criterion_j'] in {c['id'] for c in items}]
    previous={(r['criterion_i'],r['criterion_j']):r['value'] for r in old}
    changed=[(pid,actor,i['id'],j['id'],matrix[a][b]) for a,i in enumerate(items) for b,j in enumerate(items) if previous.get((i['id'],j['id']))!=matrix[a][b]]
    if changed:
        with connection.cursor() as cursor:
            cursor.executemany('''INSERT INTO comparisons(project_id,expert_id,criterion_i,criterion_j,value) VALUES(%s,%s,%s,%s,%s)
                ON CONFLICT(project_id,expert_id,criterion_i,criterion_j) DO UPDATE SET value=EXCLUDED.value''',changed)
        connection.execute('''INSERT INTO audit_events(project_id,actor_id,action,entity_type,entity_id,details)
            VALUES(%s,%s,'web_dematel_matrix','expert_matrix',%s,%s)''',(pid,actor,str(actor),Jsonb({'reason':reason,'before':old,'criteria':items,'matrix':matrix})))
    return {'changed':bool(changed),'changed_cells':len(changed)}


def sources(connection,pid,items):
    expected={(i['id'],j['id']) for i in items for j in items}
    rows=connection.execute('''SELECT u.id,u.name,u.is_active,u.email ~ '@example[.]invalid$' AS is_demo,m.role::text AS role FROM users u
        LEFT JOIN project_members m ON m.user_id=u.id AND m.project_id=%s
        WHERE (m.role IN('expert','analyst')) OR EXISTS(SELECT 1 FROM comparisons c WHERE c.project_id=%s AND c.expert_id=u.id)
        ORDER BY u.id''',(pid,pid)).fetchall()
    result=[]
    for r in rows:
        cells=[dict(x) for x in connection.execute('SELECT criterion_i,criterion_j,value FROM comparisons WHERE project_id=%s AND expert_id=%s ORDER BY criterion_i,criterion_j',(pid,r['id'])).fetchall()]
        cells=[c for c in cells if c['criterion_i'] in {i['id'] for i in items} and c['criterion_j'] in {i['id'] for i in items}]
        actual={(c['criterion_i'],c['criterion_j']) for c in cells}
        result.append({**dict(r),'count':len(cells),'complete':actual==expected,'cells':cells})
    return result


def compute_snapshot(snapshot):
    if snapshot.get('schema_version')!=1 or snapshot['parameters'].get('aggregation')!='arithmetic_mean_equal_weights':raise ValueError('Неподдерживаемый формат или правило усреднения.')
    items=snapshot['criteria'];experts=snapshot['experts'];ids=[r['id'] for r in items];codes=[r['code'] for r in items]
    if not 2<=len(items)<=10 or len(set(ids))!=len(ids) or len(set(codes))!=len(codes):raise ValueError('Неверный состав критериев снимка.')
    if not 1<=len(experts)<=20 or len({e['id'] for e in experts})!=len(experts):raise ValueError('Выберите от 1 до 20 различных экспертных матриц.')
    matrices=[]
    for expert in experts:
        cells=expert['cells'];mapping={(c['criterion_i'],c['criterion_j']):c['value'] for c in cells}
        if len(cells)!=len(ids)**2 or set(mapping)!={(i,j) for i in ids for j in ids}:raise ValueError('Экспертная матрица неполна или относится к другим критериям.')
        a=np.array([[mapping[(i,j)] for j in ids] for i in ids],dtype=float)
        if not np.isfinite(a).all() or np.any(a<0) or np.any(a>4) or np.any(a!=np.floor(a)) or np.any(np.diag(a)!=0):raise ValueError('Экспертные оценки должны быть целыми 0–4, диагональ нулевая.')
        matrices.append(a)
    direct=np.mean(matrices,axis=0)
    divisor=float(max(direct.sum(axis=0).max(),direct.sum(axis=1).max()))
    if divisor>0 and float(max(abs(np.linalg.eigvals(direct/divisor))))>=1-1e-12:
        raise ValueError('Матрица не удовлетворяет численно устойчивому условию сходимости DEMATEL: спектральный радиус должен быть меньше 1.')
    try:normalized,total,indicators=calculate_dematel(pd.DataFrame(direct,index=codes,columns=codes))
    except np.linalg.LinAlgError:raise ValueError('Не удалось решить систему DEMATEL. Проверьте оценки и сходимость.') from None
    n=normalized.to_numpy();t=total.to_numpy();radius=float(max(abs(np.linalg.eigvals(n))))
    if not np.isfinite(t).all() or not np.isfinite(indicators.to_numpy()).all():raise ValueError('DEMATEL вернул неконечные значения.')
    if not np.isclose(float(indicators.D.sum()),float(indicators.R.sum()),rtol=1e-10,atol=1e-10):raise ValueError('Не совпали контрольные суммы входящего и исходящего влияния.')
    off_diagonal=t[~np.eye(len(ids),dtype=bool)]
    threshold=snapshot['parameters']['threshold']
    threshold=float(off_diagonal.mean()) if threshold is None else float(threshold)
    if not np.isfinite(threshold) or not 0<=threshold<=1e9:raise ValueError('Порог связей должен быть конечным числом от 0 до 1 000 000 000.')
    sum_prominence=float(indicators['D_plus_R'].sum())
    records=[]
    for code,row in indicators.iterrows():
        balance=float(row.D_minus_R)
        records.append({'code':code,**row.to_dict(),'group':'cause' if balance>1e-9 else ('effect' if balance< -1e-9 else 'neutral'),
            'weight':float(row.D_plus_R/sum_prominence)})
    edges=[{'from':codes[i],'to':codes[j],'value':float(t[i,j])} for i in range(len(ids)) for j in range(len(ids)) if i!=j and t[i,j]>threshold]
    return plain({'direct':table(pd.DataFrame(direct,index=codes,columns=codes)),'normalized':table(normalized),'total':table(total),'indicators':records,
        'divisor':float(max(direct.sum(axis=0).max(),direct.sum(axis=1).max())),'spectral_radius':radius,
        'sum_D':float(indicators.D.sum()),'sum_R':float(indicators.R.sum()),'threshold':threshold,'edges':edges,
        'residual_max':float(np.max(abs(t-n@t-n)))})


def threshold_value(raw):
    if not raw.strip():return None
    try:
        if len(raw)>40:raise ValueError()
        value=Decimal(raw.strip().replace(',','.'))
        if not value.is_finite() or not 0<=value<=Decimal('1e9'):raise ValueError()
        return float(value)
    except (ValueError,InvalidOperation):raise ValueError('Порог связей: число от 0 до 1 000 000 000 или пустое поле.') from None


def run(connection,pid,actor,selected,threshold):
    items=criteria(connection,pid)
    if not 1<=len(selected)<=20 or len(set(selected))!=len(selected):raise ValueError('Выберите от 1 до 20 различных матриц.')
    available={r['id']:r for r in sources(connection,pid,items)}
    if any(i not in available or not available[i]['complete'] for i in selected):raise ValueError('Выбрана чужая или неполная экспертная матрица.')
    project=dict(connection.execute('SELECT id,title,goal,budget,revision,catwoe,root_definition FROM projects WHERE id=%s',(pid,)).fetchone())
    snapshot=plain({'schema_version':1,'project':project,'criteria':items,'experts':[available[i] for i in sorted(selected)],
        'parameters':{'aggregation':'arithmetic_mean_equal_weights','threshold':threshold,'classification_tolerance':1e-9},
        'producer':{'dematel_core_sha256':core_hash(),'web_logic_version':'dematel-web-1','requested_by':actor,'numpy':np.__version__,'pandas':pd.__version__}})
    output=compute_snapshot(snapshot);fingerprint=digest(snapshot)
    existing=connection.execute("SELECT * FROM structure_models WHERE project_id=%s AND method='dematel' AND input->>'input_fingerprint'=%s AND created_by=%s ORDER BY id",(pid,fingerprint,actor)).fetchall()
    if existing:
        if len(existing)!=1:raise ValueError('Найдено несколько одинаковых результатов DEMATEL.')
        if existing[0]['input'].get('snapshot')!=snapshot:raise ValueError('Сохранённый снимок не совпал.')
        assert_close(existing[0]['output'],output)
        return {'structure_id':existing[0]['id'],'created':False,'project_revision':project['revision']}
    rid=connection.execute('''INSERT INTO structure_models(project_id,method,input,output,project_revision,created_by)
        VALUES(%s,'dematel',%s,%s,%s,%s) RETURNING id''',(pid,Jsonb({'snapshot':snapshot,'input_fingerprint':fingerprint}),Jsonb(output),project['revision'],actor)).fetchone()['id']
    connection.execute('''INSERT INTO audit_events(project_id,actor_id,action,entity_type,entity_id,details)
        VALUES(%s,%s,'web_dematel_calculate','structure_model',%s,%s)''',(pid,actor,str(rid),Jsonb({'experts':sorted(selected),'input_fingerprint':fingerprint})))
    return {'structure_id':rid,'created':True,'project_revision':project['revision']}


def load(connection,pid,rid):
    row=connection.execute('''SELECT s.*,u.name AS actor_name FROM structure_models s JOIN users u ON u.id=s.created_by
        WHERE s.project_id=%s AND s.id=%s AND s.method='dematel' ''',(pid,rid)).fetchone()
    if not row:return None
    snapshot=row['input'].get('snapshot')
    if not snapshot:raise ValueError('Этот результат DEMATEL имеет иной формат снимка.')
    if snapshot['producer']['dematel_core_sha256']!=core_hash() or snapshot['producer']['web_logic_version']!='dematel-web-1':raise ValueError('Для проверки нужна сохранённая версия модуля DEMATEL из Git.')
    if digest(snapshot)!=row['input'].get('input_fingerprint') or snapshot['project']['revision']!=row['project_revision']:raise ValueError('Снимок DEMATEL или его ревизия не совпали.')
    output=compute_snapshot(snapshot);assert_close(row['output'],output)
    current=connection.execute('SELECT revision FROM projects WHERE id=%s',(pid,)).fetchone()['revision']
    return {'record':dict(row),'snapshot':snapshot,'output':output,'is_stale':current!=row['project_revision'],'current_revision':current,'verified':True}
