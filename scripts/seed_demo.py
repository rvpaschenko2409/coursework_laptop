"""Load the subject demo atomically; keep ML predictions for the next stage."""
from __future__ import annotations

import argparse
import copy
import csv
import getpass
import hashlib
import json
import math
from pathlib import Path
import secrets
import sys

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]
LOCK_KEY = 27001801
FEATURES = ('cpu_cores', 'cpu_threads', 'cpu_frequency_ghz', 'ram_gb', 'storage_type', 'storage_gb')
CODES = ('price_rub', 'performance_score', 'mass_kg', 'battery_hours', 'warranty_months')
DEMATEL_CODES = ('price', 'performance', 'mass', 'battery_life', 'warranty')


def fingerprint(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        if not fields or len(fields) != len(set(fields)):
            raise ValueError(f'Повторяющиеся или отсутствующие заголовки: {path.name}')
        rows = list(reader)
        if any(None in row or any(value is None for value in row.values()) for row in rows):
            raise ValueError(f'Неверное число полей: {path.name}')
        return fields, rows


def positive(text: object) -> float:
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Числовое значение должно быть положительным и конечным.')
    return value


def load_payload(root: Path = ROOT) -> dict:
    payload = json.loads((root / 'data/demo/project.json').read_text(encoding='utf-8-sig'))
    if tuple(row['code'] for row in payload['criteria']) != CODES:
        raise ValueError('Состав и порядок критериев демонстрации изменены.')
    fields, rows = read_csv(root / 'data/decision/alternatives.csv')
    required = {'alternative_id', 'name', 'data_origin', *FEATURES, *CODES[:1], *CODES[2:]}
    if not required.issubset(fields) or len(rows) != 5:
        raise ValueError('Ожидаются пять альтернатив с полным составом характеристик.')
    if len({row['alternative_id'] for row in rows}) != len(rows):
        raise ValueError('Повтор идентификатора альтернативы.')
    if {row['alternative_id'] for row in rows} != {'A1', 'A2', 'A3', 'A4', 'A5'}:
        raise ValueError('Ожидаются альтернативы A1–A5.')
    payload['alternatives'], payload['scores'] = [], []
    for order, row in enumerate(rows):
        if row['data_origin'] != 'synthetic' or row['storage_type'] not in ('SSD', 'HDD'):
            raise ValueError('Учебные альтернативы должны иметь явное синтетическое происхождение.')
        if not row['name'].strip():
            raise ValueError('Пустое название альтернативы.')
        features = {'storage_type': row['storage_type'], 'data_origin': 'synthetic'}
        for name in FEATURES:
            if name == 'storage_type':
                continue
            value = positive(row[name])
            if name != 'cpu_frequency_ghz':
                if not value.is_integer():
                    raise ValueError(f'{name}: требуется целое значение.')
                value = int(value)
            features[name] = value
        if features['cpu_threads'] < features['cpu_cores']:
            raise ValueError('Количество потоков меньше количества ядер.')
        payload['alternatives'].append(dict(code=row['alternative_id'], name=row['name'],
            description='Вымышленная конфигурация учебного сценария; не рыночное предложение.',
            features=features, sort_order=order))
        for code in CODES:
            if code != 'performance_score':
                payload['scores'].append(dict(alternative_code=row['alternative_id'], criterion_code=code,
                                             value=positive(row[code]), source='manual'))
    columns, matrix = read_csv(root / 'data/decision/dematel_direct.csv')
    if columns != ['criterion', *DEMATEL_CODES] or [row['criterion'] for row in matrix] != list(DEMATEL_CODES):
        raise ValueError('Неверный состав или порядок критериев DEMATEL.')
    mapping = dict(zip(DEMATEL_CODES, CODES))
    payload['comparisons'] = []
    for row in matrix:
        for column in DEMATEL_CODES:
            value = float(row[column])
            if not math.isfinite(value) or not value.is_integer() or not 0 <= value <= 4:
                raise ValueError('Оценка DEMATEL должна быть целой от 0 до 4.')
            if row['criterion'] == column and value != 0:
                raise ValueError('Ненулевая диагональ DEMATEL.')
            payload['comparisons'].append(dict(criterion_i=mapping[row['criterion']],
                                               criterion_j=mapping[column], value=int(value)))
    payload['budget'] = positive(payload['budget'])
    # Canonical order makes fingerprints independent of row retrieval order and CRLF/LF.
    return canonical(payload)


def canonical(payload: dict) -> dict:
    result = copy.deepcopy(payload)
    keys = {'users': lambda x: x['email'], 'criteria': lambda x: x['sort_order'],
            'alternatives': lambda x: x['sort_order'],
            'scores': lambda x: (x['alternative_code'], x['criterion_code']),
            'comparisons': lambda x: (x['criterion_i'], x['criterion_j']),
            'stakeholders': lambda x: x['name']}
    for name, key in keys.items():
        result[name] = sorted(result[name], key=key)
    return result


def read_project(connection, project_id: int) -> dict:
    row = connection.execute('SELECT title,goal,budget,catwoe,root_definition,settings FROM projects WHERE id=%s', (project_id,)).fetchone()
    settings = row.pop('settings')
    payload = dict(row, key=settings['demo_key'], version=settings['demo_version'])
    queries = {
        'users': '''SELECT u.email,u.name,m.role::text AS role FROM project_members m JOIN users u ON u.id=m.user_id WHERE m.project_id=%s''',
        'criteria': '''SELECT code,name,unit,direction::text AS direction,source::text AS source,sort_order FROM criteria WHERE project_id=%s''',
        'alternatives': '''SELECT code,name,description,features,sort_order FROM alternatives WHERE project_id=%s''',
        'scores': '''SELECT a.code AS alternative_code,c.code AS criterion_code,s.value,s.source::text AS source
                    FROM scores s JOIN alternatives a ON a.id=s.alternative_id JOIN criteria c ON c.id=s.criterion_id WHERE s.project_id=%s''',
        'comparisons': '''SELECT ci.code AS criterion_i,cj.code AS criterion_j,x.value FROM comparisons x
                         JOIN criteria ci ON ci.id=x.criterion_i JOIN criteria cj ON cj.id=x.criterion_j WHERE x.project_id=%s''',
        'stakeholders': '''SELECT name,stakeholder_role,interests,power,legitimacy,urgency FROM stakeholders WHERE project_id=%s''',
    }
    for name, query in queries.items():
        payload[name] = [dict(item) for item in connection.execute(query, (project_id,)).fetchall()]
    return canonical(payload)


def seed(connection, payload: dict) -> dict:
    """Caller owns the transaction. No commits and no deletion inside this function."""
    connection.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK_KEY,))
    version = connection.execute('SELECT version_num FROM alembic_version').fetchone()
    if not version or version['version_num'] != '0001':
        raise ValueError('Ожидается миграция 0001. Загрузка остановлена.')
    digest = fingerprint(payload)
    existing = connection.execute("SELECT id,revision,settings FROM projects WHERE settings->>'demo_key'=%s FOR UPDATE", (payload['key'],)).fetchall()
    if len(existing) > 1:
        raise ValueError('Найдено несколько демонстрационных проектов с одним ключом.')
    if existing:
        project = existing[0]
        if project['settings'].get('demo_fingerprint') != digest:
            raise ValueError('Исходные файлы демонстрации изменены. Существующий проект не перезаписан.')
        if project['revision'] != project['settings'].get('demo_revision') or fingerprint(read_project(connection, project['id'])) != digest:
            raise ValueError('Демонстрационный проект изменён в БД. Его данные не перезаписаны.')
        return dict(project_id=project['id'], created=False, criteria=5, alternatives=5, scores=20, comparisons=25, stakeholders=4)
    ids = {}
    for user in payload['users']:
        found = connection.execute('SELECT id,name,is_active,password_hash FROM users WHERE email=%s', (user['email'],)).fetchone()
        if found:
            if found['is_active'] or found['name'] != user['name'] or not found['password_hash'].startswith('!demo-disabled:'):
                raise ValueError('Учебный email занят другой учётной записью; загрузка отменена.')
            ids[user['role']] = found['id']
        else:
            ids[user['role']] = connection.execute('''INSERT INTO users(email,name,password_hash,is_active)
                VALUES(%s,%s,%s,false) RETURNING id''', (user['email'],user['name'],'!demo-disabled:'+secrets.token_hex(32))).fetchone()['id']
    project_id = connection.execute('''INSERT INTO projects(owner_id,title,goal,budget,catwoe,root_definition,settings)
        VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id''', (ids['analyst'],payload['title'],payload['goal'],payload['budget'],
        Jsonb(payload['catwoe']),payload['root_definition'],Jsonb({'demo_key':payload['key'],'demo_version':payload['version'],
        'demo_fingerprint':digest,'data_origin':'synthetic','ml_predictions_pending':True}))).fetchone()['id']
    for role, user_id in ids.items():
        connection.execute('INSERT INTO project_members(project_id,user_id,role) VALUES(%s,%s,%s)', (project_id,user_id,role))
    criteria, alternatives = {}, {}
    for criterion in payload['criteria']:
        criteria[criterion['code']] = connection.execute('''INSERT INTO criteria(project_id,code,name,unit,direction,source,sort_order)
            VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id''', (project_id,criterion['code'],criterion['name'],criterion['unit'],
            criterion['direction'],criterion['source'],criterion['sort_order'])).fetchone()['id']
    for alternative in payload['alternatives']:
        alternatives[alternative['code']] = connection.execute('''INSERT INTO alternatives(project_id,code,name,description,features,sort_order)
            VALUES(%s,%s,%s,%s,%s,%s) RETURNING id''', (project_id,alternative['code'],alternative['name'],alternative['description'],
            Jsonb(alternative['features']),alternative['sort_order'])).fetchone()['id']
    with connection.cursor() as cursor:
        cursor.executemany('''INSERT INTO scores(project_id,alternative_id,criterion_id,expert_id,value,source)
            VALUES(%s,%s,%s,%s,%s,%s)''', [(project_id,alternatives[s['alternative_code']],criteria[s['criterion_code']],
                ids['analyst'],s['value'],s['source']) for s in payload['scores']])
        cursor.executemany('''INSERT INTO comparisons(project_id,expert_id,criterion_i,criterion_j,value) VALUES(%s,%s,%s,%s,%s)''',
            [(project_id,ids['expert'],criteria[s['criterion_i']],criteria[s['criterion_j']],s['value']) for s in payload['comparisons']])
        cursor.executemany('''INSERT INTO stakeholders(project_id,name,stakeholder_role,interests,power,legitimacy,urgency)
            VALUES(%s,%s,%s,%s,%s,%s,%s)''', [(project_id,s['name'],s['stakeholder_role'],s['interests'],s['power'],s['legitimacy'],s['urgency'])
                for s in payload['stakeholders']])
    connection.execute("UPDATE projects SET settings=settings || jsonb_build_object('demo_revision',revision+1) WHERE id=%s", (project_id,))
    if fingerprint(read_project(connection, project_id)) != digest:
        raise ValueError('Проверка загруженного проекта не пройдена; транзакция будет отменена.')
    connection.execute('''INSERT INTO audit_events(project_id,actor_id,action,entity_type,entity_id,details)
        VALUES(%s,%s,'seed_demo','project',%s,%s)''', (project_id,ids['analyst'],str(project_id),Jsonb({'fingerprint':digest,'version':payload['version']})))
    return dict(project_id=project_id, created=True, criteria=5, alternatives=5, scores=20, comparisons=25, stakeholders=4)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-files', action='store_true', help='Only validate local inputs')
    parser.add_argument('--host', default='localhost')
    parser.add_argument('--port', type=int, default=5432)
    args = parser.parse_args()
    payload = load_payload()
    if args.check_files:
        print('Исходные файлы проверены: 5 критериев, 5 ноутбуков, 20 оценок, 25 влияний, 4 участника.')
        print('SHA-256 содержимого:', fingerprint(payload))
        print('База данных не изменялась. Прогнозы ML подключаются следующим этапом.')
        return
    password = getpass.getpass('Пароль coursework_migrator: ')
    conninfo = make_conninfo(host=args.host,port=args.port,dbname='coursework_laptop',user='coursework_migrator',password=password,connect_timeout=10)
    with psycopg.connect(conninfo, row_factory=dict_row) as connection:
        result = seed(connection, payload)
    # Print only after successful COMMIT, including deferred constraints.
    print('Демонстрационный проект загружен.' if result['created'] else 'Демонстрационный проект уже существует; дубликаты не созданы.')
    print(json.dumps(result,ensure_ascii=False))
    print('Прогнозы производительности пока не загружены: ждут регистрации ML-модели.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, psycopg.Error) as exc:
        if isinstance(exc, psycopg.Error):
            print(f'Ошибка PostgreSQL ({exc.sqlstate or "подключение"}). Изменения не сохранены.', file=sys.stderr)
            if exc.diag.message_primary:
                print(exc.diag.message_primary, file=sys.stderr)
        else:
            print(f'Ошибка: {exc}', file=sys.stderr)
        raise SystemExit(1)
