"""Versioned pure adapter: ID-aligned weights and the existing MARCOS/VIKOR core."""
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
import hashlib
import numpy as np
import pandas as pd
from decision_core.criteria import CRITERIA, MINIMIZE
from decision_core.marcos import calculate_marcos
from decision_core.vikor import calculate_vikor
from decision_core.normalization import normalize_ratio, normalize_minmax
from decision_store.calculations import (matrix_from_snapshot, compute, table, ranking,
    plain, digest, core_version, assert_close)
from web_app.dematel_store import compute_snapshot as dematel_compute, core_hash as dematel_hash

LABELS={'merec':'MEREC','manual':'Ручные веса','dematel':'DEMATEL','combined':'Комбинированные веса'}
VERSION='weights-web-1'


def version():
    source=Path(__file__).read_text(encoding='utf-8-sig')
    return digest({'base_core':core_version(),'adapter':hashlib.sha256(source.encode()).hexdigest(),'version':VERSION})


def number(raw,label,maximum=Decimal('1e9')):
    try:
        if not isinstance(raw,str) or not raw.strip() or len(raw)>40:raise ValueError()
        value=Decimal(raw.strip().replace(',','.'))
        if not value.is_finite() or not 0<=value<=maximum:raise ValueError()
        return value
    except (ValueError,InvalidOperation):
        raise ValueError(f'{label}: требуется конечное неотрицательное число не больше {maximum}.') from None


def alpha_value(raw):
    value=number(raw,'α',Decimal(1))
    if value!=value.quantize(Decimal('0.0000001')):raise ValueError('α: используйте не больше 7 знаков после запятой.')
    return float(value)


def manual_weights(raw,items):
    ids=[str(c['id']) for c in items]
    if len(set(ids))!=len(ids) or set(raw)!=set(ids):raise ValueError('Ручные веса должны содержать ровно все идентификаторы критериев проекта.')
    with localcontext() as ctx:
        ctx.prec=60
        values={key:number(raw[key],next(c['name'] for c in items if str(c['id'])==key)) for key in ids}
        total=sum(values.values())
        if total<=0:raise ValueError('Хотя бы один ручной вес должен быть положительным.')
        return {key:float(value/total) for key,value in values.items()}


def dematel_weights(reference,items,project):
    saved=reference['snapshot']
    if saved['project']['id']!=project['id'] or saved['project']['revision']!=project['revision']:raise ValueError('DEMATEL относится к другой версии проекта. Сначала пересчитайте DEMATEL для текущих данных.')
    if saved['producer']['dematel_core_sha256']!=dematel_hash() or saved['producer']['web_logic_version']!='dematel-web-1':raise ValueError('Не совпала версия ядра DEMATEL.')
    if digest(saved)!=reference['input_fingerprint']:raise ValueError('Отпечаток источника DEMATEL не совпал.')
    fields=('id','code','name','unit','direction')
    def identity(rows):return {r['id']:tuple(r[k] for k in fields) for r in rows}
    if len(saved['criteria'])!=len(items) or identity(saved['criteria'])!=identity(items):raise ValueError('Критерии DEMATEL не соответствуют идентификаторам и составу критериев расчёта.')
    result=dematel_compute(saved);assert_close(reference['output'],result)
    by_code={r['code']:r['weight'] for r in result['indicators']}
    return {str(c['id']):by_code[c['code']] for c in items}


def compute_weighted(snapshot):
    matrix,excluded=matrix_from_snapshot(snapshot)
    if snapshot['matrix']!=table(matrix):raise ValueError('Матрица снимка не совпала с исходными оценками.')
    selection=snapshot['weight_selection'];method=selection['method'];items=snapshot['criteria']
    if selection.get('version')!=VERSION or method not in ('manual','dematel','combined'):raise ValueError('Неизвестная версия или метод выбора весов.')
    if snapshot['parameters']['weight_method']!=method:raise ValueError('Метод весов не совпал с параметрами.')
    objective=None;merec=None;alpha=None
    if method=='combined':
        # Reuse the tested objective calculation, including its budget filter.
        merec=compute(snapshot)['merec']
        by_code={r['criterion']:r['weight'] for r in merec['weights']}
        objective={str(c['id']):by_code[c['code']] for c in items}
        alpha=alpha_value(selection['alpha'])
        if snapshot['parameters'].get('alpha')!=alpha:raise ValueError('α не совпала с параметрами снимка.')
        source=selection['subjective_source']
    else:source=method
    if source=='manual':subjective=manual_weights(selection['raw_manual'],items)
    elif source=='dematel':subjective=dematel_weights(selection['dematel'],items,snapshot['project'])
    else:raise ValueError('Субъективный источник должен быть ручным или DEMATEL.')
    effective={key:(alpha*value+(1-alpha)*objective[key] if method=='combined' else value) for key,value in subjective.items()}
    values=np.array(list(effective.values()))
    if not np.isfinite(values).all() or np.any(values<0) or not np.isclose(values.sum(),1,atol=1e-10,rtol=0):raise ValueError('Итоговые веса должны быть неотрицательными с суммой 1.')
    weights=pd.Series({c['code']:effective[str(c['id'])] for c in items}).reindex(list(CRITERIA))
    marcos=calculate_marcos(matrix,weights);vikor,conditions=calculate_vikor(matrix,weights,v=snapshot['parameters']['v'])
    normalized=normalize_ratio(matrix);distances=1-normalize_minmax(matrix)
    rows=[{'criterion_id':c['id'],'criterion':c['code'],'weight':effective[str(c['id'])],
        'subjective':subjective[str(c['id'])],'objective':objective[str(c['id'])] if objective else None} for c in items]
    return plain({'merec':merec,'weighting':{'method':method,'subjective_source':source,'alpha':alpha,'weights':rows},
        'marcos':{'ranking':ranking(marcos),'normalized':table(normalized),'weighted':table(normalized.mul(weights,axis='columns')),
            'ideal':{c:float(matrix[c].min() if c in MINIMIZE else matrix[c].max()) for c in CRITERIA},
            'anti_ideal':{c:float(matrix[c].max() if c in MINIMIZE else matrix[c].min()) for c in CRITERIA},
            'S_ideal':float(weights.sum()),'S_anti_ideal':float(sum(matrix[c].min()/matrix[c].max()*weights[c] for c in CRITERIA))},
        'vikor':{'ranking':ranking(vikor),'conditions':conditions,'distances':table(distances),'weighted_distances':table(distances.mul(weights,axis='columns'))},
        'excluded_codes':excluded})
