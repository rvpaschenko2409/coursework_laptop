"""Scoped registry CRUD; complete before/after records stay in the audit journal."""
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from decision_store.calculations import plain
from web_app.alternative_validation import InvalidForm
from web_app.stakeholder_validation import (FIELDS,RULE,ATTRIBUTES,CLASSES,scores,normalized_name,reason)

ACTIONS={'web_stakeholder_create':'Добавление','web_stakeholder_update':'Изменение','web_stakeholder_delete':'Удаление'}


def decorated(row):
    if row is None:return None
    value=dict(row);derived=scores(value)
    if float(value['salience'])!=derived['salience']:raise ValueError('Средняя значимость записи не совпала с оценками.')
    return plain({**value,**derived})


def item(connection,pid,sid):
    row=connection.execute('SELECT * FROM stakeholders WHERE project_id=%s AND id=%s',(pid,sid)).fetchone()
    if not row:raise HTTPException(404,'Заинтересованная сторона не найдена в этом проекте.')
    return decorated(row)


def listing(connection,pid,page=1):
    rows=connection.execute('''SELECT * FROM stakeholders WHERE project_id=%s
        ORDER BY salience DESC,id LIMIT 21 OFFSET %s''',(pid,(page-1)*20)).fetchall()
    counts=connection.execute('''SELECT count(*) AS total,
        count(*) FILTER(WHERE (power>=3)::integer+(legitimacy>=3)::integer+(urgency>=3)::integer=0) AS absent,
        count(*) FILTER(WHERE (power>=3)::integer+(legitimacy>=3)::integer+(urgency>=3)::integer=1) AS latent,
        count(*) FILTER(WHERE (power>=3)::integer+(legitimacy>=3)::integer+(urgency>=3)::integer=2) AS expectant,
        count(*) FILTER(WHERE (power>=3)::integer+(legitimacy>=3)::integer+(urgency>=3)::integer=3) AS definitive
        FROM stakeholders WHERE project_id=%s''',(pid,)).fetchone()
    return {'rows':[decorated(r) for r in rows[:20]],'has_next':len(rows)>20,'page':page,'counts':dict(counts),'rule':RULE}


def writable(connection,pid):
    row=connection.execute('SELECT status FROM projects WHERE id=%s',(pid,)).fetchone()
    if not row or row['status']=='archived':raise InvalidForm({'project':'Архивный проект доступен только для чтения.'})


def save(connection,pid,actor,values,previous=None):
    writable(connection,pid)
    existing=connection.execute('SELECT id,name FROM stakeholders WHERE project_id=%s',(pid,)).fetchall()
    if any(normalized_name(r['name'])==normalized_name(values['name']) and (not previous or r['id']!=previous['id']) for r in existing):
        raise InvalidForm({'name':'Сторона с таким названием уже есть в проекте. Регистр и лишние пробелы не различаются.'})
    if not previous and len(existing)>=100:raise InvalidForm({'project':'В одном проекте допускается не больше 100 заинтересованных сторон.'})
    if previous and all(previous[k]==values[k] for k in FIELDS):
        return {'stakeholder_id':previous['id'],'created':False,'changed':False}
    params=tuple(values[k] for k in FIELDS)
    if previous:
        connection.execute('''UPDATE stakeholders SET name=%s,stakeholder_role=%s,interests=%s,power=%s,legitimacy=%s,urgency=%s
            WHERE project_id=%s AND id=%s''',(*params,pid,previous['id']))
        sid=previous['id'];action='web_stakeholder_update'
    else:
        sid=connection.execute('''INSERT INTO stakeholders(project_id,name,stakeholder_role,interests,power,legitimacy,urgency)
            VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id''',(pid,*params)).fetchone()['id'];action='web_stakeholder_create'
    after=item(connection,pid,sid)
    revision=connection.execute('SELECT revision FROM projects WHERE id=%s',(pid,)).fetchone()['revision']
    connection.execute('''INSERT INTO audit_events(project_id,actor_id,action,entity_type,entity_id,details)
        VALUES(%s,%s,%s,'stakeholder',%s,%s)''',(pid,actor,action,str(sid),Jsonb({'before':previous,'after':after,
            'reason':values['reason'],'rule':RULE,'project_revision':revision})))
    return {'stakeholder_id':sid,'created':previous is None,'changed':True,'project_revision':revision}


def remove(connection,pid,actor,previous,raw_reason,confirmation):
    writable(connection,pid);why=reason(raw_reason)
    if confirmation!=previous['name']:raise InvalidForm({'confirm_name':'Введите точное название удаляемой стороны.'})
    connection.execute('DELETE FROM stakeholders WHERE project_id=%s AND id=%s',(pid,previous['id']))
    revision=connection.execute('SELECT revision FROM projects WHERE id=%s',(pid,)).fetchone()['revision']
    connection.execute('''INSERT INTO audit_events(project_id,actor_id,action,entity_type,entity_id,details)
        VALUES(%s,%s,'web_stakeholder_delete','stakeholder',%s,%s)''',(pid,actor,str(previous['id']),
            Jsonb({'before':previous,'after':None,'reason':why,'rule':RULE,'project_revision':revision})))
    return {'stakeholder_id':previous['id'],'deleted':True,'project_revision':revision}


def history(connection,pid,page=1):
    rows=connection.execute('''SELECT a.*,u.name AS actor_name FROM audit_events a JOIN users u ON u.id=a.actor_id
        WHERE a.project_id=%s AND a.entity_type='stakeholder' AND a.action IN(%s,%s,%s)
        ORDER BY a.id DESC LIMIT 21 OFFSET %s''',(pid,*ACTIONS,(page-1)*20)).fetchall()
    return {'rows':[{**dict(r),'action_label':ACTIONS[r['action']]} for r in rows[:20]],'has_next':len(rows)>20,'page':page}
