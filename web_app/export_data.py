"""Read-only XLSX input from verified historical calculation or choice snapshots."""
from fastapi import HTTPException
from decision_store.calculations import digest
from web_app.calculation_store import load_pair
from web_app.decision_choice_store import load as load_choice
from web_app.sensitivity_store import source as source_snapshot
from web_app.sensitivity_math import verify_source


def from_calculation(connection,pid,rid):
    data=load_pair(connection,pid,rid)
    if data is None:raise HTTPException(404,'Расчёт не найден в этом проекте.')
    source=source_snapshot(data);canonical=data['results']['marcos']['id']
    row=connection.execute('SELECT id FROM decision_choices WHERE project_id=%s AND result_id=%s ORDER BY id DESC LIMIT 1',(pid,canonical)).fetchone()
    choice=load_choice(connection,pid,row['id']) if row else None
    if choice and choice['context']['source']!=source:raise ValueError('Снимок решения ЛПР не совпал с исходным расчётом.')
    return bundle(source,data['current_revision'],choice)


def from_choice(connection,pid,cid):
    choice=load_choice(connection,pid,cid)
    if choice is None:raise HTTPException(404,'Решение не найдено в этом проекте.')
    return bundle(choice['context']['source'],choice['current_revision'],choice)


def bundle(source,current_revision,choice=None):
    calculated=verify_source(source)
    return {'source':source,'calculated':calculated,'choice':choice,'current_revision':current_revision,
        'is_stale':source['input']['project']['revision']!=current_revision,'source_fingerprint':digest(source)}
