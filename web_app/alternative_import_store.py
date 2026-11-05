"""Dry-run import validation and atomic confirmed creation through the ordinary laptop store."""
import base64
import hashlib
import hmac
import json
import secrets
import time
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from web_app import alternative_store
from web_app.alternative_validation import FEATURES, InvalidForm, validate_form, in_training_profiles
from web_app.criterion_store import manual_fields
from web_app.laptop_predictions import active_model, predict
from web_app.alternative_import_files import read_table

TTL = 15 * 60


def columns(connection, pid):
    return ['code', 'name', 'description', *FEATURES, *manual_fields(connection, pid)]


def model_identity(model):
    return None if model is None else {k: model[k] for k in ('id', 'algorithm', 'artifact_sha256', 'artifact_path', 'feature_names', 'origin')}


def validate_rows(connection, pid, raw, reason):
    manual = manual_fields(connection, pid)
    existing = {r['code'] for r in connection.execute('SELECT code FROM alternatives WHERE project_id=%s', (pid,)).fetchall()}
    model = active_model(connection, pid)
    budget = connection.execute('SELECT budget FROM projects WHERE id=%s', (pid,)).fetchone()['budget']
    codes = [row.get('code', '').upper() for _, row in raw]
    output = []; values = []
    reason = reason.strip()
    if not 3 <= len(reason) <= 500:
        raise ValueError('Укажите причину импорта длиной 3–500 символов.')
    for line, row in raw:
        errors = {}; warnings = []; result = None; predicted = None
        try:
            result = validate_form({**row, 'performance_mode': 'auto', 'change_reason': reason}, manual=manual)
        except InvalidForm as exc:
            errors.update(exc.errors)
        code = row.get('code', '').upper()
        if codes.count(code) > 1:errors['code'] = 'Код повторяется в файле.'
        if code in existing:errors['code'] = 'Код уже есть в проекте. Импорт добавляет только новые ноутбуки.'
        if len(existing) + len(raw) > 15:errors['limit'] = 'После импорта будет больше 15 ноутбуков. Уменьшите число строк.'
        if result and not errors:
            if model:
                try:predicted = predict(model, result['features'])
                except ValueError as exc:errors['model'] = str(exc)
            else:warnings.append('Нет активной ML-модели: производительность останется незаполненной.')
            if not in_training_profiles(result['features']):warnings.append('Характеристики выходят за профили учебной синтетики.')
            if budget is not None and result['scores']['price_rub'] > float(budget):warnings.append('Цена выше бюджета: ноутбук сохранится, но будет исключён при расчёте.')
        output.append({'line': line, 'code': code, 'name': row.get('name', ''), 'fields': row,
                       'errors': errors, 'warnings': warnings, 'prediction': predicted})
        values.append(result)
    return output, values, model_identity(model)


def encode(payload, key):
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    data = base64.urlsafe_b64encode(raw).decode().rstrip('=')
    signature = hmac.new(key, data.encode(), hashlib.sha256).hexdigest()
    return data + '.' + signature


def decode(token, key):
    try:
        if not isinstance(token, str) or len(token) > 128 * 1024:raise ValueError()
        data, supplied = token.split('.')
        expected = hmac.new(key, data.encode('ascii'), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(supplied, expected):raise ValueError()
        payload = json.loads(base64.urlsafe_b64decode(data + '=' * (-len(data) % 4)))
        if not isinstance(payload, dict) or payload.get('version') != 1:raise ValueError()
        return payload
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        raise HTTPException(422, 'Предпросмотр недействителен. Загрузите файл и проверьте его заново.') from None


def preview(connection, pid, actor, revision, content, filename, reason, key):
    headers = columns(connection, pid)
    raw, format_name = read_table(content, filename, [h for h in headers if h != 'description'])
    rows, values, model = validate_rows(connection, pid, raw, reason)
    valid = not any(r['errors'] for r in rows)
    payload = {'version': 1, 'project_id': pid, 'actor_id': actor, 'revision': revision,
               'expires': int(time.time()) + TTL, 'request_key': secrets.token_urlsafe(24),
               'model': model, 'rows': raw, 'reason': reason.strip(), 'filename': filename,
               'file_sha256': hashlib.sha256(content).hexdigest()}
    return {'rows': rows, 'valid': valid, 'format_name': format_name, 'filename': filename,
            'token': encode(payload, key) if valid else None, 'reason': reason.strip(), 'model': model}


def confirm(connection, pid, actor, token, key):
    payload = decode(token, key)
    if (payload['project_id'], payload['actor_id']) != (pid, actor):
        raise HTTPException(403, 'Предпросмотр относится к другому проекту или пользователю.')
    # The caller holds the project lock. Retried successful confirmations return the same IDs.
    previous = connection.execute("""SELECT details FROM audit_events WHERE project_id=%s AND actor_id=%s
        AND action='web_alternative_import' AND details->>'request_key'=%s""", (pid, actor, payload['request_key'])).fetchone()
    if previous:
        return {'created': False, 'alternative_ids': previous['details']['alternative_ids']}
    if payload['expires'] < time.time():
        raise HTTPException(409, 'Предпросмотр старше 15 минут. Загрузите файл заново.')
    alternative_store.check_revision(connection, pid, payload['revision'])
    rows, values, model = validate_rows(connection, pid, payload['rows'], payload['reason'])
    if model != payload['model']:
        raise HTTPException(409, 'Активная ML-модель изменилась. Сначала обновите предпросмотр.')
    if any(r['errors'] for r in rows):
        raise HTTPException(422, 'Данные больше не проходят проверку. Загрузите файл заново.')
    ids = []
    for value in values:
        result = alternative_store.save(connection, pid, actor, value)
        ids.append(result['alternative_id'])
    connection.execute('''INSERT INTO audit_events(project_id,actor_id,action,entity_type,entity_id,details)
        VALUES(%s,%s,'web_alternative_import','project',%s,%s)''',
        (pid, actor, str(pid), Jsonb({'request_key': payload['request_key'], 'alternative_ids': ids,
         'filename': payload['filename'], 'file_sha256': payload['file_sha256'], 'reason': payload['reason'],
         'source_revision': payload['revision'], 'model': model, 'rows': len(ids)})))
    return {'created': True, 'alternative_ids': ids}
