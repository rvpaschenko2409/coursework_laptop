"""Training upload passport and explicit raw/staging layers, independent of models."""
import hashlib
import json
import math
import re
from collections import Counter
from urllib.parse import urlsplit

import numpy as np
from psycopg.types.json import Jsonb

from web_app.alternative_import_files import read_table
from web_app.alternative_validation import FEATURES, HARDWARE, number
from web_app.project_store import audit, insert_row

MAX_FILE = 20 * 1024 * 1024
MAX_ROWS = 10000
TARGET = 'performance_score'
HEADERS = [*FEATURES, TARGET]
OPTIONAL = ['synthetic_score', 'sample_id', 'configuration_id', 'data_origin']


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def passport(fields):
    result = {k: fields.get(k, '').strip() for k in ('name','origin','source_url','license','seed','generator_version','reason')}
    for key, lower, upper in [('name',2,200),('license',3,1000),('reason',3,500)]:
        if not lower <= len(result[key]) <= upper or any(ord(c)<32 for c in result[key]):
            label={'name':'Название','license':'Условия использования','reason':'Цель загрузки'}[key]
            raise ValueError(f'{label}: требуется от {lower} до {upper} печатных символов.')
    if result['origin'] not in ('synthetic','open','manual'):
        raise ValueError('Укажите происхождение: синтетические, открытые или введённые вручную данные.')
    url = result['source_url']
    if url:
        try:
            parts = urlsplit(url)
            if len(url)>2000 or parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password or any(ord(c)<33 for c in url):raise ValueError()
        except ValueError:
            raise ValueError('Ссылка на источник должна начинаться с http:// или https://, без паролей.') from None
    if result['origin']=='open' and not url:
        raise ValueError('Для открытых данных обязательна ссылка на источник и лицензия.')
    if result['origin']=='synthetic':
        if not re.fullmatch(r'[0-9]{1,10}',result['seed']) or int(result['seed'])>2147483647:
            raise ValueError('Seed синтетики: целое число от 0 до 2147483647.')
        if not 1<=len(result['generator_version'])<=200 or any(ord(c)<32 for c in result['generator_version']):
            raise ValueError('Для синтетики укажите версию или имя генератора (до 200 символов).')
        result['seed'] = int(result['seed'])
    else:
        if result['seed'] or result['generator_version']:
            raise ValueError('Seed и генератор заполняются только для синтетических данных.')
        result['seed'] = None
        result['generator_version'] = None
    return result


def inspect(content, filename, fields):
    meta = passport(fields)
    rows, label = read_table(content, filename, FEATURES, [TARGET,*OPTIONAL], max_rows=MAX_ROWS,max_file=MAX_FILE)
    columns = list(rows[0][1])
    targets = set(columns) & {TARGET,'synthetic_score'}
    if len(targets)!=1:
        raise ValueError('Нужен ровно один целевой столбец: performance_score или synthetic_score.')
    source_target = next(iter(targets))
    if source_target=='synthetic_score' and meta['origin']!='synthetic':
        raise ValueError('Столбец synthetic_score допускается только для синтетических данных.')
    if 'data_origin' in columns and any(row['data_origin']!=meta['origin'] for _,row in rows):
        raise ValueError('Значения data_origin должны совпадать с выбранным происхождением.')
    missing = Counter({key:0 for key in columns})
    invalid = Counter({key:0 for key in [*FEATURES,source_target]})
    kinds = {key:Counter() for key in columns}
    sample_ids = Counter(); sample_values = {}; conflicting_ids = set()
    raw = []; clean = []; issues = []; seen = set(); duplicates = 0; groups = set()
    numeric = {key:[] for key in [*HARDWARE,TARGET]}
    for index, (line, row) in enumerate(rows,1):
        raw.append({'row_number':index,'data':row,'provenance':{'file_line':line}})
        for key,value in row.items():
            if not value:missing[key]+=1;kinds[key]['empty']+=1
            else:
                try:
                    parsed=float(value.replace(',','.'))
                    kind='number' if math.isfinite(parsed) else 'nonfinite'
                except ValueError:kind='text'
                kinds[key][kind]+=1
        errors=[]; typed={}
        for key,args in HARDWARE.items():
            try:typed[key]=number(row[key],*args)
            except ValueError as exc:invalid[key]+=1;errors.append(str(exc))
        storage=row['storage_type']
        if storage not in ('SSD','HDD'):invalid['storage_type']+=1;errors.append('storage_type: только SSD или HDD.')
        typed['storage_type']=storage
        if typed.get('cpu_threads',256)<typed.get('cpu_cores',0):
            invalid['cpu_threads']+=1;errors.append('cpu_threads: потоков меньше, чем ядер.')
        try:typed[TARGET]=number(row[source_target],'Целевая производительность',0.000001,1e9)
        except ValueError as exc:invalid[source_target]+=1;errors.append(str(exc))
        if 'sample_id' in row:
            sid=row['sample_id']
            if not sid or len(sid)>100:errors.append('sample_id: непустая строка до 100 символов.')
            sample_ids[sid]+=1
            if sid in sample_values and sample_values[sid]!=digest(row):conflicting_ids.add(sid)
            sample_values[sid]=digest(row)
            typed['sample_id']=sid
        identity=digest(row)
        duplicate=identity in seen;seen.add(identity)
        if duplicate:duplicates+=1;errors.append('Полный дубликат строки: исключён из проверенного слоя.')
        if errors:
            if len(issues)<100:issues.append({'line':line,'errors':errors})
            continue
        group=digest({key:typed[key] for key in FEATURES})
        groups.add(group)
        typed['configuration_id']=group
        typed['data_origin']=meta['origin']
        clean.append({'row_number':index,'data':typed,'provenance':{'file_line':line,'configuration_id':group}})
        for key in numeric:numeric[key].append(typed[key])
    # Reused sample identifiers with different values are ambiguous: do not invent identity.
    repeated_ids=sum(n-1 for sid,n in sample_ids.items() if sid and n>1)
    ranges={}; outliers={}
    for key,values in numeric.items():
        if values:
            q1,q3=np.quantile(values,[.25,.75]);iqr=q3-q1
            ranges[key]={'min':min(values),'max':max(values),'median':float(np.median(values))}
            outliers[key]=int(sum(v<q1-1.5*iqr or v>q3+1.5*iqr for v in values))
    quality={'version':1,'raw_rows':len(raw),'staging_rows':len(clean),'excluded_rows':len(raw)-len(clean),
             'columns':columns,'column_types':{k:dict(v) for k,v in kinds.items()},'missing':dict(missing),
             'invalid_cells':dict(invalid),'duplicate_rows':duplicates,'repeated_sample_ids':repeated_ids,
             'ranges':ranges,'iqr_outliers':outliers,'configurations':len(groups),
             'origin':meta['origin'],'real_world_accuracy_established':False,
             'policy':'Exclude invalid rows and full duplicates; no imputation, clipping, or target invention. IQR flags are descriptive only.',
             'source_target':source_target,'target':TARGET,'split_assigned':False}
    errors=[]
    if len(clean)<20:errors.append('После проверки требуется не меньше 20 строк для будущего обучения.')
    if len(groups)<10:errors.append('Требуется не меньше 10 различных конфигураций для группового разделения и кросс-валидации.')
    if conflicting_ids:errors.append('Одинаковые sample_id относятся к разным строкам. Исправьте идентификаторы.')
    sha=hashlib.sha256(content).hexdigest()
    return {'meta':meta,'filename':filename,'file_sha256':sha,'format_name':label,'quality':quality,
            'raw':raw,'staging':clean,'issues':issues,'errors':errors,'valid':not errors,
            'sample':raw[:10],'semantic_sha256':digest([r['data'] for r in clean])}


def listing(connection,pid):
    return connection.execute('''SELECT id,name,origin,source_url,license,seed,generator_version,sha256,
        row_count,column_count,uploaded_at,quality_report,generation_params FROM datasets
        WHERE project_id=%s ORDER BY uploaded_at DESC,id DESC''',(pid,)).fetchall()


def save(connection,pid,actor,review):
    if not review['valid']:raise ValueError('Данные не прошли проверку.')
    previous=connection.execute('SELECT id FROM datasets WHERE project_id=%s AND sha256=%s',(pid,review['file_sha256'])).fetchone()
    if previous:return {'dataset_id':previous['id'],'created':False}
    meta=review['meta']
    did=insert_row(connection,'datasets',dict(project_id=pid,name=meta['name'],origin=meta['origin'],
        source_url=meta['source_url'] or None,license=meta['license'],sha256=review['file_sha256'],seed=meta['seed'],
        generator_version=meta['generator_version'],generation_params={
            'upload_version':1,'filename':review['filename'],'format':review['format_name'],
            'semantic_sha256':review['semantic_sha256'],'feature_names':FEATURES,'target_name':TARGET,
            'source_target':review['quality']['source_target'],'split_assigned':False,
            'provenance_note':'User-declared origin, license, seed and generator; not independently verified.'},
        quality_report=review['quality'],row_count=review['quality']['raw_rows'],
        column_count=len(review['quality']['columns']),uploaded_by=actor))
    for layer,key in [('raw','raw'),('staging','staging')]:
        data=review[key]
        for start in range(0,len(data),500):
            connection.execute('''INSERT INTO dataset_rows(dataset_id,layer,row_number,data,provenance)
                SELECT %s,%s::data_layer,x.row_number,x.data,x.provenance
                FROM jsonb_to_recordset(%s::jsonb) AS x(row_number integer,data jsonb,provenance jsonb)''',
                (did,layer,Jsonb(data[start:start+500])))
    audit(connection,pid,actor,'web_training_data_upload',{'dataset_id':did,'sha256':review['file_sha256'],
        'reason':meta['reason'],'raw_rows':len(review['raw']),'staging_rows':len(review['staging']),
        'origin':meta['origin'],'model_trained':False})
    return {'dataset_id':did,'created':True}
