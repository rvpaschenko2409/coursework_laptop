"""Finite, one-at-a-time sensitivity on an immutable decision snapshot."""
from decision_core.compatibility import LEGACY_CORE, LEGACY_WEIGHTS
from decimal import Decimal,InvalidOperation
from pathlib import Path
import hashlib
import numpy as np
import pandas as pd
from decision_core.marcos import calculate_marcos
from decision_core.vikor import calculate_vikor
from decision_store.calculations import (compute as compute_merec,matrix_from_snapshot,
    core_version,digest,plain,assert_close,ranking)
from web_app import weight_math

VERSION='sensitivity-web-1'
RULE='multiply_one_weight_then_normalize_all'


def version():
    return digest({'logic':VERSION,'source':hashlib.sha256(Path(__file__).read_text(encoding='utf-8-sig').encode()).hexdigest(),
        'base_core':core_version(),'weight_adapter':weight_math.version()})


def step_value(raw):
    try:
        if not isinstance(raw,str) or len(raw)>40:raise ValueError()
        value=Decimal(raw.strip().replace(',','.'))
        if not value.is_finite() or not Decimal('0.01')<=value<=25:raise ValueError()
        if value!=value.quantize(Decimal('0.01')):raise ValueError()
        return float(value)
    except (ValueError,InvalidOperation):
        raise ValueError('Шаг изменения веса: число от 0,01 до 25 процентов, не больше двух знаков после запятой.') from None


def perturb(weights,criterion_id,delta):
    if criterion_id not in weights or not np.isfinite(delta) or not -.5<=delta<=.5:raise ValueError('Неверный критерий или относительное изменение веса.')
    if not np.isfinite(list(weights.values())).all() or any(v<0 for v in weights.values()) or not np.isclose(sum(weights.values()),1,atol=1e-10,rtol=0):raise ValueError('Некорректные исходные веса.')
    if delta==0 or weights[criterion_id] in (0,1):return dict(weights)
    result={key:value*(1+delta if key==criterion_id else 1) for key,value in weights.items()}
    total=sum(result.values())
    if not np.isfinite(list(result.values())).all() or any(v<0 for v in result.values()) or total<=0:raise ValueError('Некорректный набор весов сценария.')
    return {key:value/total for key,value in result.items()}


def verify_source(source):
    snap=source['input'];weighted='weight_selection' in snap
    expected_version=weight_math.version() if weighted else core_version()
    saved_version=snap['producer'].get('weight_adapter_version' if weighted else 'core_version')
    if saved_version not in (expected_version,LEGACY_WEIGHTS if weighted else LEGACY_CORE):raise ValueError('Изменилась версия исходного расчётного модуля. Нужна сохранённая версия из Git.')
    expected_version=saved_version
    calculated=weight_math.compute_weighted(snap) if weighted else compute_merec(snap)
    expected_weighting=calculated.get('weighting') or {'method':'merec','alpha':None,'weights':calculated['merec']['weights']}
    assert_close(source['weighting'],expected_weighting,'source.weighting')
    records=source['records']
    if len(records)!=2 or {r['method'] for r in records}!={'marcos','vikor'} or len({r['id'] for r in records})!=2:raise ValueError('Нужна полная исходная пара MARCOS и VIKOR.')
    actor=records[0]['created_by'];wid=source['weight_set']['id']
    for r in records:
        if r['core_version']!=expected_version or r['status']!='succeeded' or r['project_id']!=snap['project']['id'] or r['project_revision']!=snap['project']['revision'] or r['created_by']!=actor or r['params'].get('weight_set_id')!=wid:raise ValueError('Нарушена целостность источника анализа.')
        assert_close(source['outputs'][r['method']],calculated[r['method']],'source.'+r['method'])
    if source['weight_set']['method']!=expected_weighting['method'] or source['weight_set']['project_revision']!=snap['project']['revision']:raise ValueError('Источник весов не соответствует исходной версии проекта.')
    assert_close(source['weight_set']['alpha'],expected_weighting['alpha'],'source.alpha')
    return calculated


def compare(rows,base):
    previous={r['alternative_code']:r['rank'] for r in base};current={r['alternative_code']:r['rank'] for r in rows}
    if set(current)!=set(previous):raise ValueError('Состав альтернатив сценария изменён.')
    deltas={code:current[code]-previous[code] for code in previous}
    leaders=[r['alternative_code'] for r in rows if r['rank']==1]
    original=[r['alternative_code'] for r in base if r['rank']==1]
    return {'leaders':leaders,'leaders_changed':set(leaders)!=set(original),'changed_ranks':sum(d!=0 for d in deltas.values()),
        'max_shift':max(abs(d) for d in deltas.values()),'rank_deltas':deltas}


def compute(snapshot):
    if snapshot.get('schema_version')!=1 or snapshot['parameters'].get('rule')!=RULE:raise ValueError('Неизвестный формат или правило анализа чувствительности.')
    source=snapshot['source'];base_input=source['input'];original=verify_source(source)
    if snapshot['project']!=base_input['project']:raise ValueError('Проект анализа не совпал с исходным снимком.')
    step=step_value(str(snapshot['parameters']['step_percent']))/100
    matrix,excluded=matrix_from_snapshot(base_input)
    items=base_input['criteria'];by_code={c['code']:c['id'] for c in items}
    weights={by_code[r['criterion']]:r['weight'] for r in source['weighting']['weights']}
    if set(weights)!={c['id'] for c in items} or any(v<0 for v in weights.values()) or not np.isfinite(list(weights.values())).all() or not np.isclose(sum(weights.values()),1,atol=1e-10,rtol=0):raise ValueError('Неверные исходные веса.')
    original_v=base_input['parameters']['v']
    scenarios=[]

    def evaluate(sid,kind,selected,v,criterion_id=None,delta=None):
        series=pd.Series({c['code']:selected[c['id']] for c in items})
        marcos=ranking(calculate_marcos(matrix,series));vikor,conditions=calculate_vikor(matrix,series,v=v);vikor=ranking(vikor)
        change_m=compare(marcos,original['marcos']['ranking']);change_v=compare(vikor,original['vikor']['ranking'])
        row={'id':sid,'kind':kind,'criterion_id':criterion_id,'delta':delta,'v':v,
            'weights':[{'criterion_id':c['id'],'criterion':c['code'],'weight':selected[c['id']]} for c in items],
            'marcos':{'ranking':marcos,'comparison':change_m},'vikor':{'ranking':vikor,'conditions':conditions,'comparison':change_v,
                'compromise_changed':set(conditions['compromise_set'])!=set(original['vikor']['conditions']['compromise_set'])}}
        scenarios.append(row)

    evaluate('base','base',weights,original_v)
    for method in ('marcos','vikor'):assert_close(scenarios[0][method]['ranking'],original[method]['ranking'],'baseline.'+method)
    assert_close(scenarios[0]['vikor']['conditions'],original['vikor']['conditions'],'baseline.conditions')
    for c in items:
        for suffix,multiple in [('m2',-2),('m1',-1),('p1',1),('p2',2)]:
            delta=multiple*step;evaluate(f"weight_{c['id']}_{suffix}",'weight',perturb(weights,c['id'],delta),original_v,c['id'],delta)
    grid=sorted(set([i/10 for i in range(11)]+[original_v]))
    for i,v in enumerate(grid):evaluate(f'v_{i}','v',weights,v)
    summaries={}
    for kind in ('weight','v'):
        rows=[r for r in scenarios if r['kind']==kind]
        summaries[kind]={'count':len(rows),'marcos_leader_changes':sum(r['marcos']['comparison']['leaders_changed'] for r in rows),
            'vikor_leader_changes':sum(r['vikor']['comparison']['leaders_changed'] for r in rows),
            'vikor_compromise_changes':sum(r['vikor']['compromise_changed'] for r in rows),
            'marcos_order_changes':sum(r['marcos']['comparison']['changed_ranks']>0 for r in rows),
            'vikor_order_changes':sum(r['vikor']['comparison']['changed_ranks']>0 for r in rows)}
    ranges=[]
    for code in matrix.index:
        ranges.append({'code':code,**{method:{'min':min(next(r['rank'] for r in s[method]['ranking'] if r['alternative_code']==code) for s in scenarios),
            'max':max(next(r['rank'] for r in s[method]['ranking'] if r['alternative_code']==code) for s in scenarios)} for method in ('marcos','vikor')}})
    return plain({'scenarios':scenarios,'summary':summaries,'rank_ranges':ranges,'excluded_codes':excluded,'v_grid':grid,
        'limitations':['Проверена конечная сетка сценариев. Это не вероятность выбора и не доказательство устойчивости при любых весах.',
            'Матрица, бюджет и сохранённые прогнозы ML фиксированы. Ошибки прогнозов этим экспериментом не оцениваются.']})
