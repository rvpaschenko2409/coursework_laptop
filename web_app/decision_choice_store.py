"""Append-only choices, idempotent retries and explicit replacement of prior choices."""
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from decision_store.calculations import digest,plain
from web_app.calculation_store import load_pair
from web_app.sensitivity_store import source as source_snapshot
from web_app import decision_choice_logic as logic


def latest(connection,pid):
    row=connection.execute('SELECT id,alternative_code,decided_by,decided_at FROM decision_choices WHERE project_id=%s ORDER BY id DESC LIMIT 1',(pid,)).fetchone()
    return dict(row) if row else None


def run(connection,pid,rid,actor,code,explanation,anchor,acknowledge):
    # The caller holds the project lock and rechecks role, session, CSRF and revision.
    project=connection.execute('SELECT status FROM projects WHERE id=%s',(pid,)).fetchone()
    if not project or project['status']=='archived':raise ValueError('В архивном проекте нельзя фиксировать новое решение.')
    data=load_pair(connection,pid,rid)
    if data is None:raise HTTPException(404,'Исходный расчёт не найден в этом проекте.')
    if data['is_stale']:raise ValueError('Расчёт устарел. Попросите аналитика пересчитать рейтинги для текущей версии проекта.')
    if acknowledge!='yes':raise ValueError('Подтвердите, что вы ознакомились с рейтингами и ограничениями прогноза.')
    explanation=logic.reason(explanation);previous=logic.previous_id(anchor)
    source=source_snapshot(data);picked=logic.selected(source,code)
    choice={'alternative_code':code,'reason':explanation,'decided_by':actor['id'],'actor_name':actor['name'],
        'previous_choice_id':previous,'acknowledged':True,'role_at_decision':'decision_maker'}
    context={'schema_version':1,'choice':choice,'source':source,'selected':picked,
        'producer':{'entrypoint':'web_decision_choice','version':logic.version()}}
    fingerprint=digest(context);canonical=data['results']['marcos']['id']
    existing=connection.execute("SELECT id FROM decision_choices WHERE project_id=%s AND decided_by=%s AND alternative_snapshot->>'input_fingerprint'=%s ORDER BY id",(pid,actor['id'],fingerprint)).fetchall()
    if existing:
        if len(existing)!=1:raise ValueError('Найдено несколько одинаковых записей решения.')
        saved=load(connection,pid,existing[0]['id'])
        if saved['context']!=context:raise ValueError('Сохранённое решение не соответствует исходным данным.')
        return {'choice_id':existing[0]['id'],'created':False,'alternative_code':code,'source_result_id':canonical}
    previous_choice=latest(connection,pid)
    if (previous_choice['id'] if previous_choice else 0)!=previous:raise HTTPException(409,'В проекте появилось более новое решение ЛПР. Обновите страницу и ознакомьтесь с ним.')
    sid=connection.execute('''INSERT INTO decision_choices(project_id,result_id,alternative_code,alternative_snapshot,decided_by,reason)
        VALUES(%s,%s,%s,%s,%s,%s) RETURNING id''',(pid,canonical,code,Jsonb({'context':context,'input_fingerprint':fingerprint}),actor['id'],explanation)).fetchone()['id']
    connection.execute('''INSERT INTO audit_events(project_id,actor_id,action,entity_type,entity_id,details)
        VALUES(%s,%s,'web_decision_choice','decision_choice',%s,%s)''',(pid,actor['id'],str(sid),Jsonb({'result_id':canonical,
            'alternative_code':code,'previous_choice_id':previous,'input_fingerprint':fingerprint})))
    return {'choice_id':sid,'created':True,'alternative_code':code,'source_result_id':canonical}


def load(connection,pid,sid):
    row=connection.execute('SELECT * FROM decision_choices WHERE project_id=%s AND id=%s',(pid,sid)).fetchone()
    if not row:return None
    envelope=row['alternative_snapshot'];context=envelope.get('context')
    if not context:raise ValueError('Эта историческая запись решения использует другой формат снимка.')
    if digest(context)!=envelope.get('input_fingerprint'):raise ValueError('Отпечаток сохранённого решения не совпал.')
    picked=logic.verified_context(context);source=context['source'];choice=context['choice']
    canonical=next(r['id'] for r in source['records'] if r['method']=='marcos')
    if source['input']['project']['id']!=pid or row['result_id']!=canonical or row['alternative_code']!=choice['alternative_code'] or row['decided_by']!=choice['decided_by'] or row['reason']!=choice['reason']:raise ValueError('Поля решения не соответствуют сохранённому снимку.')
    current=connection.execute('SELECT revision FROM projects WHERE id=%s',(pid,)).fetchone()['revision']
    current_choice=latest(connection,pid)
    return {'record':dict(row),'context':context,'selected':picked,'is_stale':current!=source['input']['project']['revision'],
        'current_revision':current,'is_latest':current_choice['id']==sid,'latest_choice_id':current_choice['id'],'verified':True,
        'input_fingerprint':envelope['input_fingerprint']}


def history(connection,pid,page=1):
    rows=connection.execute('''SELECT d.id,d.result_id,d.alternative_code,d.decided_by,d.decided_at,d.reason,
        d.alternative_snapshot->'context'->'choice'->>'actor_name' AS saved_actor_name,
        d.alternative_snapshot->'context'->'source'->'input'->'project'->>'revision' AS source_revision,
        u.name AS current_actor_name FROM decision_choices d JOIN users u ON u.id=d.decided_by
        WHERE d.project_id=%s ORDER BY d.id DESC LIMIT 21 OFFSET %s''',(pid,(page-1)*20)).fetchall()
    return {'rows':[dict(r) for r in rows[:20]],'has_next':len(rows)>20,'page':page,'latest_choice':latest(connection,pid)}
