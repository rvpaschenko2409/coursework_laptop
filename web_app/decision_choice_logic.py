"""Bounded user input and reproducible context of a deliberate decision-maker choice."""
from pathlib import Path
import hashlib
import unicodedata
from decision_store.calculations import digest,assert_close,plain
from web_app.sensitivity_math import verify_source
from web_app.weight_math import version as weight_version

VERSION='decision-choice-web-1'


def version():
    root=Path(__file__).resolve().parent
    return digest({'logic':VERSION,'weight_core':weight_version(),
        'files':{name:hashlib.sha256((root/name).read_text(encoding='utf-8-sig').encode()).hexdigest()
            for name in ('decision_choice_logic.py','sensitivity_math.py')}})


def reason(raw):
    if not isinstance(raw,str) or any(unicodedata.category(c) in ('Cc','Cs') and c not in '\n\r\t' for c in raw):raise ValueError('Обоснование содержит недопустимые символы.')
    value=unicodedata.normalize('NFC',raw).strip()
    if not 20<=len(value)<=2000:raise ValueError('Обоснование решения: от 20 до 2000 символов.')
    return value


def previous_id(raw):
    if not isinstance(raw,str) or not raw.isascii() or not raw.isdecimal() or len(raw)>18:raise ValueError('Не определена версия предыдущего решения. Обновите страницу.')
    return int(raw)


def selected(source,code):
    calculated=verify_source(source)
    eligible=source['input']['matrix']['rows']
    if not isinstance(code,str) or code not in eligible:raise ValueError('Выберите ноутбук из альтернатив, прошедших бюджетный фильтр исходного расчёта.')
    alternative=next(a for a in source['input']['alternatives'] if a['code']==code)
    criteria=source['input']['criteria'];ids={c['id'] for c in criteria}
    values=[r for r in source['input']['scores'] if r['alternative_id']==alternative['id'] and r['criterion_id'] in ids]
    if len(values)!=len(criteria):raise ValueError('В исходном снимке не хватает оценок выбранного ноутбука.')
    return plain({'alternative':alternative,'scores':values,
        'marcos':next(r for r in calculated['marcos']['ranking'] if r['alternative_code']==code),
        'vikor':next(r for r in calculated['vikor']['ranking'] if r['alternative_code']==code),
        'in_compromise':code in calculated['vikor']['conditions']['compromise_set'],
        'marcos_first_rank':any(r['alternative_code']==code and r['rank']==1 for r in calculated['marcos']['ranking'])})


def verified_context(context):
    if context.get('schema_version')!=1 or context['producer'].get('version')!=version():raise ValueError('Для проверки решения нужна сохранённая версия расчётных модулей из Git.')
    choice=context['choice']
    if choice.get('role_at_decision')!='decision_maker':raise ValueError('Не сохранена роль ЛПР на момент решения.')
    if reason(choice['reason'])!=choice['reason'] or choice.get('acknowledged') is not True:raise ValueError('Обоснование или подтверждение решения не совпало.')
    if type(choice['decided_by']) is not int or choice['decided_by']<=0 or type(choice['previous_choice_id']) is not int or choice['previous_choice_id']<0:raise ValueError('Некорректный автор или предыдущее решение.')
    if not isinstance(choice.get('actor_name'),str) or not choice['actor_name']:raise ValueError('Не сохранено имя автора решения.')
    expected=selected(context['source'],choice['alternative_code'])
    assert_close(context['selected'],expected,'selected')
    return expected
