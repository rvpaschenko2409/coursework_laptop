"""Transactional laptop edits; keep predictions and historical decisions traceable."""
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from web_app.alternative_validation import FEATURES,MANUAL,InvalidForm,in_training_profiles
from web_app.laptop_predictions import active_model,predict


def check_revision(connection,project_id,expected):
    try:expected=int(expected)
    except (TypeError,ValueError):raise HTTPException(422,'Обновите форму: отсутствует версия проекта.') from None
    current=connection.execute('SELECT revision FROM projects WHERE id=%s',(project_id,)).fetchone()['revision']
    if current!=expected:raise HTTPException(409,'Проект изменился после открытия формы. Откройте её заново и повторите действие.')


def ready(connection):
    row=connection.execute('SELECT version_num FROM web_alembic_version').fetchone()
    if not row or row['version_num']!='web0002':raise HTTPException(503,'Для редактирования требуется подготовить веб-модуль альтернатив.')


def alternative(connection,project_id,alternative_id):
    row=connection.execute('SELECT * FROM alternatives WHERE project_id=%s AND id=%s',(project_id,alternative_id)).fetchone()
    if not row:raise HTTPException(404,'Ноутбук не найден в этом проекте.')
    return dict(row)


def current_scores(connection,project_id,alternative_id):
    return {row['code']:dict(row) for row in connection.execute('''SELECT s.*,c.code FROM scores s
        JOIN criteria c ON c.project_id=s.project_id AND c.id=s.criterion_id
        WHERE s.project_id=%s AND s.alternative_id=%s''',(project_id,alternative_id)).fetchall()}


def audit(connection,project_id,actor,action,entity_id,details):
    connection.execute('''INSERT INTO audit_events(project_id,actor_id,action,entity_type,entity_id,details)
        VALUES(%s,%s,%s,'alternative',%s,%s)''',(project_id,actor,action,str(entity_id),Jsonb(jsonable_encoder(details))))


def write_score(connection,project_id,aid,cid,actor,value,source,prediction_id,reason,old):
    if old and (old['value'],old['source'],old['prediction_id'])==(value,source,prediction_id):return False
    if old:
        # PostgreSQL's existing trigger appends score_history with OLD prediction_id.
        connection.execute('''UPDATE scores SET value=%s,source=%s,prediction_id=%s,expert_id=%s,override_reason=%s
            WHERE project_id=%s AND id=%s''',(value,source,prediction_id,actor,reason,project_id,old['id']))
    else:
        connection.execute('''INSERT INTO scores(project_id,alternative_id,criterion_id,expert_id,value,source,prediction_id,override_reason)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',(project_id,aid,cid,actor,value,source,prediction_id,reason))
    return True


def save(connection,project_id,actor,values,existing=None,expert=False):
    aid=existing['id'] if existing else None
    before=current_scores(connection,project_id,aid) if aid else {}
    from web_app.criterion_store import items
    criteria={row['code']:row['id'] for row in items(connection,project_id)}
    reason=values['reason']
    if existing and not reason:raise InvalidForm({'change_reason':'Укажите причину изменения; она сохранится в истории.'})
    reason=reason or 'Первичный ввод ноутбука'
    model=None;old_perf=before.get('performance_score');prediction_id=None;predicted=None;fresh=False
    if not expert:
        duplicate=connection.execute('SELECT id FROM alternatives WHERE project_id=%s AND code=%s',(project_id,values['code'])).fetchone()
        if duplicate and duplicate['id']!=aid:raise InvalidForm({'code':'Такой код уже есть в проекте.'})
        if not existing and connection.execute('SELECT count(*) AS n FROM alternatives WHERE project_id=%s',(project_id,)).fetchone()['n']>=15:
            raise InvalidForm({'code':'В проекте допускается не более 15 ноутбуков.'})
        model=active_model(connection,project_id)
        previous=None
        if old_perf and old_perf['prediction_id']:
            previous=connection.execute('SELECT * FROM predictions WHERE project_id=%s AND id=%s',(project_id,old_perf['prediction_id'])).fetchone()
        if previous and previous['features_snapshot']==values['features'] and (model and model['id']==previous['model_id'] or not model and values['performance_mode']=='override'):
            prediction_id=previous['id'];predicted=previous['value']
        elif model:
            try:predicted=predict(model,values['features'])
            except ValueError as exc:raise InvalidForm({'performance_mode':str(exc)}) from exc
            fresh=True
        if predicted is None and (values['performance_mode']=='override' or old_perf):
            raise InvalidForm({'performance_mode':'Для этих характеристик нет исходного прогноза. Подключите активную модель.'})
        features={**(existing['features'] if existing else {}),**values['features']}
        if existing:
            changed=any(existing[key]!=values[key] for key in ('code','name','description')) or existing['features']!=features
            if changed:
                connection.execute('UPDATE alternatives SET code=%s,name=%s,description=%s,features=%s WHERE project_id=%s AND id=%s',
                    (values['code'],values['name'],values['description'],Jsonb(features),project_id,aid))
        else:
            row=connection.execute('''INSERT INTO alternatives(project_id,code,name,description,features,sort_order)
                SELECT %s,%s,%s,%s,%s,coalesce(max(sort_order),-1)+1 FROM alternatives WHERE project_id=%s RETURNING id''',
                (project_id,values['code'],values['name'],values['description'],Jsonb(features),project_id)).fetchone()
            aid=row['id'];changed=True
        if fresh:
            prediction_id=connection.execute('''INSERT INTO predictions(project_id,model_id,alternative_id,criterion_id,value,features_snapshot,explanation,created_by)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id''',(project_id,model['id'],aid,criteria['performance_score'],predicted,
                    Jsonb(values['features']),Jsonb({'origin':model['origin'],'within_training_profiles':in_training_profiles(values['features'])}),actor)).fetchone()['id']
    else:changed=False
    for code,value in values['scores'].items():
        changed=write_score(connection,project_id,aid,criteria[code],actor,value,'manual',None,reason,before.get(code)) or changed
    if not expert and predicted is not None:
        override=values['performance_mode']=='override'
        value=values['performance_override'] if override else predicted
        changed=write_score(connection,project_id,aid,criteria['performance_score'],actor,value,'manual' if override else 'ml',prediction_id,reason,old_perf) or changed
    if changed:
        audit(connection,project_id,actor,'web_scores_edit' if expert else ('web_alternative_edit' if existing else 'web_alternative_create'),aid,
            {'reason':reason,'before':{'alternative':existing,'scores':list(before.values())},
             'after':{'alternative':alternative(connection,project_id,aid),'scores':list(current_scores(connection,project_id,aid).values())}})
    return {'alternative_id':aid,'changed':changed,'prediction_id':prediction_id,
        'revision':connection.execute('SELECT revision FROM projects WHERE id=%s',(project_id,)).fetchone()['revision']}


def delete(connection,project_id,actor,existing,reason,confirmation):
    if confirmation!=existing['code']:raise InvalidForm({'confirm_code':'Введите код ноутбука точно, как показано выше.'})
    reason=reason.strip()
    if not 3<=len(reason)<=500:raise InvalidForm({'change_reason':'Укажите причину удаления длиной от 3 до 500 символов.'})
    aid=existing['id']
    scores=list(current_scores(connection,project_id,aid).values())
    predictions=[dict(row) for row in connection.execute('SELECT * FROM predictions WHERE project_id=%s AND alternative_id=%s ORDER BY id',(project_id,aid)).fetchall()]
    history=[dict(row) for row in connection.execute('''SELECT h.* FROM score_history h JOIN scores s ON s.project_id=h.project_id AND s.id=h.score_id
        WHERE h.project_id=%s AND s.alternative_id=%s ORDER BY h.id''',(project_id,aid)).fetchall()]
    archived=connection.execute('''INSERT INTO web_deleted_alternatives(project_id,alternative_id,code,name,snapshot,deleted_by,reason)
        VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id''',(project_id,aid,existing['code'],existing['name'],
        Jsonb(jsonable_encoder({'schema_version':1,'alternative':existing,'scores':scores,'predictions':predictions,'score_history':history})),actor,reason)).fetchone()['id']
    connection.execute('DELETE FROM score_history WHERE project_id=%s AND score_id IN(SELECT id FROM scores WHERE project_id=%s AND alternative_id=%s)',(project_id,project_id,aid))
    connection.execute('DELETE FROM scores WHERE project_id=%s AND alternative_id=%s',(project_id,aid))
    connection.execute('DELETE FROM predictions WHERE project_id=%s AND alternative_id=%s',(project_id,aid))
    connection.execute('DELETE FROM alternatives WHERE project_id=%s AND id=%s',(project_id,aid))
    audit(connection,project_id,actor,'web_alternative_delete',aid,{'reason':reason,'archive_id':archived,'code':existing['code']})
    return {'deleted':True,'archive_id':archived}
