"""Readable report sections; stored values remain literal and never become formulas."""
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from web_app.weight_math import LABELS

NAMES=['Отчёт','Рейтинги','Матрица','Веса','Промежуточные','ML и источники','Решение ЛПР']
WIDTHS=[[35,105],[16,32,16,20,16,18,18,18,20],[16,32,22,25,18,18,20],
    [30,38,18,30,20,20,20,22],[36,25,38,25],[36,38,105],[35,105]]


def row(*values,kind='body',formats=None):return {'values':list(values),'kind':kind,'formats':formats or []}


def sheet(name,title,headers):return {'name':name,'widths':WIDTHS[NAMES.index(name)],'rows':[row(),row(title,kind='title'),row(),row(*headers,kind='header')]}


def flat(value,prefix=''):
    if isinstance(value,dict):
        for key,item in value.items():yield from flat(item,f'{prefix}.{key}' if prefix else str(key))
    elif isinstance(value,list):
        # Arrays such as configuration IDs are provenance, not another observation table.
        if value and all(not isinstance(x,(dict,list)) for x in value):yield prefix,', '.join(map(str,value))
        else:
            for i,item in enumerate(value):yield from flat(item,f'{prefix}[{i}]')
    else:yield prefix,value


def stamp(value):
    if not value:return 'Не зафиксирована'
    date=datetime.fromisoformat(str(value).replace('Z','+00:00')) if not isinstance(value,datetime) else value
    if date.tzinfo is None:date=date.replace(tzinfo=timezone.utc)
    return date.astimezone(ZoneInfo('Asia/Omsk')).replace(tzinfo=None)


def make_report(data):
    source=data['source'];s=source['input'];calc=data['calculated'];p=s['project'];choice=data['choice']
    m=next(r for r in source['records'] if r['method']=='marcos');v=next(r for r in source['records'] if r['method']=='vikor')
    criteria={r['code']:r for r in s['criteria']};alternatives={a['code']:a for a in s['alternatives']}
    weights=source['weighting'];cond=calc['vikor']['conditions']
    output=sheet('Отчёт','Отчёт о выборе ноутбука',['Поле','Значение'])
    output['rows'] += [row('Проект',p['title']),row('Цель',p['goal']),row('Бюджет на устройство, руб.',p['budget']),
        row('Идентификатор проекта',p['id']),row('Расчёт MARCOS / VIKOR',f"{m['id']} / {v['id']}"),
        row('Дата расчёта, Омск (UTC+6)',stamp(m['created_at'])),row('Автор расчёта, учётная запись',m['created_by']),
        row('Версия исходных данных',p['revision']),row('Версия проекта при выгрузке',data['current_revision']),
        row('Состояние исходных данных','Данные изменились после расчёта' if data['is_stale'] else 'Соответствуют версии проекта при выгрузке'),
        row('Источник весов',LABELS[weights['method']]),row('Параметр v',s['parameters']['v']),
        row('Лидеры MARCOS',', '.join(r['alternative_code'] for r in calc['marcos']['ranking'] if r['rank']==1)),
        row('Лидер VIKOR по Q',cond['leader']),row('Компромиссный набор VIKOR',', '.join(cond['compromise_set'])),
        row('Решение ЛПР',f"№ {choice['record']['id']}: {choice['record']['alternative_code']}" if choice else 'Для этого расчёта решение ЛПР ещё не сохранено'),
        row('Смысл рейтингов','MARCOS: полезность выше — лучше. VIKOR: Q ниже — лучше. Окончательный выбор принимает ЛПР.'),
        row('Формат отчёта','Сохранённые значения расчёта. Редактирование ячеек не запускает повторный расчёт методов.'),
        row('Версия математического ядра',m['core_version']),row('SHA-256 контекста расчёта',data['source_fingerprint'])]
    for warning in s['limitations']:output['rows'].append(row('Ограничение',warning))
    output['rows'] += [row('Ограничения задачи (CATWOE E)',p.get('catwoe',{}).get('E')),
        row('Корневое определение',p.get('root_definition'))]
    rankings=sheet('Рейтинги','Сравнение MARCOS и VIKOR',['Код','Ноутбук','Ранг MARCOS','Полезность','Ранг VIKOR','S VIKOR','R VIKOR','Q VIKOR','В компромиссе'])
    vik={r['alternative_code']:r for r in calc['vikor']['ranking']}
    for r in calc['marcos']['ranking']:
        code=r['alternative_code'];t=vik[code]
        rankings['rows'].append(row(code,alternatives[code]['name'],r['rank'],r['utility'],t['rank'],t['S'],t['R'],t['Q'],code in cond['compromise_set'],formats=['text','text','integer','number','integer']))
    rankings['chart_rows']=len(calc['marcos']['ranking'])
    rankings['filter_end']=len(rankings['rows'])
    rankings['rows'] += [row(),row('Условия VIKOR','Значение',kind='header')]
    labels={'leader':'Лидер по Q','compromise_set':'Компромиссный набор','acceptable_advantage':'Приемлемое преимущество','acceptable_stability':'Приемлемая устойчивость','advantage_threshold':'Порог DQ','gap_to_second':'Разрыв Q со вторым местом'}
    for key,value in cond.items():rankings['rows'].append(row(labels.get(key,key),', '.join(map(str,value)) if isinstance(value,list) else value))
    matrix=sheet('Матрица','Исходная матрица после бюджетного фильтра',['Код','Ноутбук']+[criteria[c]['name']+' ('+criteria[c]['unit']+')' for c in s['matrix']['columns']])
    for code,values in zip(s['matrix']['rows'],s['matrix']['values']):matrix['rows'].append(row(code,alternatives[code]['name'],*values))
    matrix['rows'] += [row(),row('Все оценки исходного снимка','Критерий','Значение','Единица','Источник','Прогноз №','Модель №',kind='header')]
    byid={a['id']:a for a in s['alternatives']};cbid={c['id']:c for c in s['criteria']};pred={r['id']:r for r in s['predictions']}
    for value in s['scores']:
        a=byid[value['alternative_id']];c=cbid[value['criterion_id']];pr=pred.get(value.get('prediction_id'))
        label='Прогноз ML' if value['source']=='ml' else ('Ручная корректировка прогноза' if pr else ('Расчёт' if value['source']=='calc' else 'Ручной ввод'))
        matrix['rows'].append(row(a['code'],c['name'],value['value'],c['unit'],label,value.get('prediction_id'),pr['model_id'] if pr else None))
    matrix['rows'] += [row(),row('Исключены по бюджету','Ноутбук',kind='header')]
    for code in calc['excluded_codes']:matrix['rows'].append(row(code,alternatives[code]['name']))
    if not calc['excluded_codes']:matrix['rows'].append(row('Нет исключённых альтернатив'))
    matrix['rows'] += [row(),row('Характеристики всех альтернатив','Поле','Значение',kind='header')]
    for a in s['alternatives']:
        for key,value in a['features'].items():matrix['rows'].append(row(a['code'],key,value))
    ws=sheet('Веса','Критерии и применённые веса',['Код','Критерий','Направление','Единица','Итоговый вес','Субъективный','MEREC','Эффект исключения'])
    for r in weights['weights']:
        c=criteria[r['criterion']]
        ws['rows'].append(row(c['code'],c['name'],c['direction'],c['unit'],r['weight'],r.get('subjective'),r.get('objective'),r.get('removal_effect')))
    ws['rows'] += [row(),row('Источник весов',LABELS[weights['method']]),row('α',weights.get('alpha'))]
    selection=s.get('weight_selection',{})
    for key in ['reason','subjective_source','raw_manual']:
        if key in selection:
            for path,value in flat(selection[key],key):ws['rows'].append(row(path,value))
    if selection.get('dematel'):
        ref=selection['dematel'];ws['rows'].append(row('Источник DEMATEL №',ref['structure_id']))
        ws['rows'].append(row('Версия данных DEMATEL',ref['snapshot']['project']['revision']))
        for e in ref['snapshot']['experts']:ws['rows'].append(row('Эксперт (учебный, если is_demo)',e['name'],e['is_demo']))
    steps=sheet('Промежуточные','Промежуточные вычисления',['Метод / показатель','Альтернатива / строка','Критерий / поле','Значение'])
    for method in ['merec','marcos','vikor']:
        result=calc.get(method)
        if result is None:continue
        for key,value in result.items():
            if key in ('ranking','conditions','weights'):continue
            if isinstance(value,dict) and {'rows','columns','values'}<=value.keys():
                for name,values in zip(value['rows'],value['values']):
                    for col,number in zip(value['columns'],values):steps['rows'].append(row(method+' / '+key,name,col,number))
            else:
                for path,number in flat(value):steps['rows'].append(row(method+' / '+key,'',path,number))
    ml=sheet('ML и источники','Модели, метрики и происхождение данных',['Объект','Показатель','Значение'])
    if not s['models']:ml['rows'].append(row('Модели','Состояние','В исходном расчёте нет связанных прогнозов ML'))
    for model in s['models']:
        title=f"ML № {model['id']} / {model['name']}"
        fields={'algorithm':model['algorithm'],'version':model['version'],'trained_at':stamp(model.get('trained_at')),
            'registered_at':stamp(model.get('registered_at')),'dataset_id':model['dataset_id'],'artifact_sha256':model['artifact_sha256'],
            'target_name':model['target_name'],'seed':model['seed'],'feature_names':model['feature_names'],
            'metrics':model['metrics'],'params':model['params'],'environment':model['environment']}
        split=model.get('split_manifest') or {}
        fields['split']={k:split[k] for k in ('method','cv_method','seed','test_size') if k in split}
        fields['split']['train_rows']=len(split['train_sample_ids']) if isinstance(split.get('train_sample_ids'),list) else None;fields['split']['test_rows']=len(split['test_sample_ids']) if isinstance(split.get('test_sample_ids'),list) else None
        for path,value in flat(fields):ml['rows'].append(row(title,path,value))
    for d in s['datasets']:
        for key,value in flat(d):ml['rows'].append(row(f"Данные № {d['id']}",key,value))
    for pr in s['predictions']:
        for key,value in flat({k:pr[k] for k in ('value','model_id','alternative_id','criterion_id','created_at','features_snapshot','explanation')}):ml['rows'].append(row(f"Прогноз № {pr['id']}",key,value))
    decision=sheet('Решение ЛПР','Сохранённый выбор ЛПР',['Поле','Значение'])
    if not choice:decision['rows'].append(row('Состояние','Для этой пары расчётов решение ЛПР не сохранено.'))
    else:
        r=choice['record'];c=choice['context']['choice'];sel=choice['selected']
        decision['rows'] += [row('Решение №',r['id']),row('Автор',c['actor_name']),row('Учётная запись',c['decided_by']),
            row('Роль на момент решения','ЛПР'),row('Дата, Омск (UTC+6)',stamp(r['decided_at'])),row('Выбранный ноутбук',c['alternative_code']),
            row('Название',sel['alternative']['name']),row('Обоснование',c['reason']),row('Ранг MARCOS',sel['marcos']['rank']),row('Ранг VIKOR',sel['vikor']['rank']),
            row('В компромиссном наборе',sel['in_compromise']),row('Последнее решение проекта',choice['is_latest']),row('Прежнее решение №',c['previous_choice_id'] or None),
            row('SHA-256 контекста решения',choice['input_fingerprint'])]
    return [output,rankings,matrix,ws,steps,ml,decision]
