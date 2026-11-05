"""Current criteria, explicit scores, and reversible removal preserving immutable results."""
import re
from decimal import Decimal, InvalidOperation
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from web_app.alternative_store import write_score

PROTECTED = {'price_rub': 'min', 'performance_score': 'max'}
RESERVED = {'csrf','revision','code','name','description','change_reason','reason','unit','direction','confirmation','performance_mode','performance_override','cpu_cores','cpu_threads','cpu_frequency_ghz','ram_gb','storage_gb','storage_type'}


def items(connection, pid, archived=False):
    hidden = connection.execute('SELECT settings FROM projects WHERE id=%s', (pid,)).fetchone()['settings'].get('archived_criteria', [])
    return [dict(r) for r in connection.execute('SELECT * FROM criteria WHERE project_id=%s ORDER BY sort_order,id', (pid,)).fetchall()
            if (r['id'] in hidden) == archived]


def manual_fields(connection, pid):
    from web_app.alternative_validation import MANUAL
    return {c['code']: (c['name'] + (', ' + c['unit'] if c['unit'] else ''), *MANUAL[c['code']][1:])
            if c['code'] in MANUAL else (c['name'] + (', ' + c['unit'] if c['unit'] else ''), 0.000001, 1e12, False)
            for c in items(connection, pid) if c['code'] != 'performance_score'}


def positive(raw):
    try:
        if not isinstance(raw, str) or len(raw) > 40:
            raise ValueError()
        value = Decimal(raw.strip().replace(',', '.'))
        if not value.is_finite() or not Decimal('0.000001') <= value <= Decimal('1e12'):
            raise ValueError()
        return value
    except (ValueError, InvalidOperation):
        raise HTTPException(422, 'Оценки: положительные числа от 0,000001 до 1 000 000 000 000.') from None


def validate(form, existing=None):
    code = form.get('code', '').strip()
    name = form.get('name', '').strip()
    unit = form.get('unit', '').strip()
    direction = form.get('direction', '')
    reason = form.get('reason', '').strip()
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,49}', code):
        raise HTTPException(422, 'Код: строчная латинская буква, затем буквы, цифры или подчёркивания; до 50 символов.')
    if code in RESERVED or code.startswith(('score_', 'weight_', 'cell_')):
        raise HTTPException(422, 'Этот код зарезервирован для служебных полей и характеристик ноутбука.')
    if existing and code != existing['code']:
        raise HTTPException(422, 'Код сохранённого критерия изменять нельзя.')
    if not 2 <= len(name) <= 200 or len(unit) > 50 or any(ord(c) < 32 for c in name + unit):
        raise HTTPException(422, 'Название: 2–200 печатных символов, единица измерения: до 50.')
    if direction not in ('min', 'max') or code in PROTECTED and direction != PROTECTED[code]:
        raise HTTPException(422, 'Цена минимизируется, производительность максимизируется; выберите min или max.')
    if not 3 <= len(reason) <= 500 or any(ord(c) < 32 and c not in '\n\r\t' for c in reason):
        raise HTTPException(422, 'Укажите причину изменения длиной 3–500 символов.')
    return dict(code=code, name=name, unit=unit, direction=direction), reason


def audit(connection, pid, actor, action, cid, details):
    connection.execute('''INSERT INTO audit_events(project_id,actor_id,action,entity_type,entity_id,details)
        VALUES(%s,%s,%s,'criterion',%s,%s)''', (pid, actor, action, str(cid), Jsonb(details)))


def save(connection, pid, actor, form, existing=None):
    fields, reason = validate(form, existing)
    if existing and fields['code'] in {'price_rub','performance_score','mass_kg','battery_hours','warranty_months'} and fields['unit'] != existing['unit']:
        raise HTTPException(422, 'Единицы исходного шаблона фиксированы. Добавьте отдельный критерий для другой шкалы.')
    active = items(connection, pid)
    if not existing and len(active) >= 10:
        raise HTTPException(422, 'Допускается от 2 до 10 действующих критериев.')
    duplicate = connection.execute('SELECT id FROM criteria WHERE project_id=%s AND (code=%s OR name=%s)',
                                   (pid, fields['code'], fields['name'])).fetchall()
    if any(r['id'] != (existing['id'] if existing else None) for r in duplicate):
        raise HTTPException(422, 'Код или название уже заняты, в том числе удалённым критерием. Его можно восстановить.')
    alternatives = connection.execute('SELECT id FROM alternatives WHERE project_id=%s ORDER BY id', (pid,)).fetchall()
    expected = {f"score_{a['id']}" for a in alternatives} if fields['code'] != 'performance_score' else set()
    if {k for k in form if k.startswith('score_')} != expected:
        raise HTTPException(422, 'Заполните оценки всех ноутбуков этого проекта без посторонних идентификаторов.')
    values = {key: positive(form[key]) for key in expected}
    from web_app.alternative_validation import MANUAL,number
    if fields['code'] in MANUAL:
        try:
            for key in expected:number(form[key], *MANUAL[fields['code']])
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc
    old = None if not existing else {k: str(existing[k]) for k in fields}
    if existing:
        cid = existing['id']
        if any(fields[k] != existing[k] for k in fields):
            connection.execute('UPDATE criteria SET name=%s,unit=%s,direction=%s WHERE project_id=%s AND id=%s',
                               (fields['name'], fields['unit'], fields['direction'], pid, cid))
    else:
        cid = connection.execute('''INSERT INTO criteria(project_id,code,name,unit,direction,source,sort_order)
            SELECT %s,%s,%s,%s,%s,'manual',coalesce(max(sort_order),-1)+1 FROM criteria WHERE project_id=%s RETURNING id''',
            (pid, fields['code'], fields['name'], fields['unit'], fields['direction'], pid)).fetchone()['id']
    scores = {r['alternative_id']: dict(r) for r in connection.execute('SELECT * FROM scores WHERE project_id=%s AND criterion_id=%s', (pid, cid)).fetchall()}
    if fields['code'] != 'performance_score':
        for a in alternatives:
            write_score(connection, pid, a['id'], cid, actor, values[f"score_{a['id']}"], 'manual', None, reason, scores.get(a['id']))
    audit(connection, pid, actor, 'web_criterion_edit' if existing else 'web_criterion_create', cid,
          {'reason': reason, 'before': old, 'after': fields})
    return cid


def remove_or_restore(connection, pid, actor, row, form, restore=False):
    if form.get('confirmation') != row['code']:
        raise HTTPException(422, 'Введите точный код критерия для подтверждения.')
    reason = form.get('reason', '').strip()
    if not 3 <= len(reason) <= 500:
        raise HTTPException(422, 'Причина: 3–500 символов.')
    active = items(connection, pid)
    if restore and len(active) >= 10 or not restore and (len(active) <= 2 or row['code'] in PROTECTED):
        raise HTTPException(422, 'Нужно сохранить 2–10 критериев. Цена и прогнозная производительность обязательны.')
    settings = dict(connection.execute('SELECT settings FROM projects WHERE id=%s', (pid,)).fetchone()['settings'])
    hidden = set(settings.get('archived_criteria', []))
    hidden.discard(row['id']) if restore else hidden.add(row['id'])
    settings['archived_criteria'] = sorted(hidden)
    connection.execute('UPDATE projects SET settings=%s WHERE id=%s', (Jsonb(settings), pid))
    audit(connection, pid, actor, 'web_criterion_restore' if restore else 'web_criterion_remove', row['id'], {'reason': reason, 'code': row['code']})
