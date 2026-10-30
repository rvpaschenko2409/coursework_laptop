"""Register trusted existing models and data; do not fit estimators or replace files."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import sys
import warnings

import joblib
import numpy as np
import pandas as pd
import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from sklearn.ensemble import RandomForestRegressor
from sklearn.exceptions import InconsistentVersionWarning
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline

from scripts.seed_demo import LOCK_KEY, fingerprint, load_payload, read_project

ROOT = Path(__file__).resolve().parents[1]
FEATURES = ['cpu_cores', 'cpu_threads', 'cpu_frequency_ghz', 'ram_gb', 'storage_gb', 'storage_type']
GROUP_FEATURES = ['cpu_cores', 'cpu_threads', 'cpu_frequency_ghz', 'ram_gb', 'storage_type', 'storage_gb']
MODELS = ('ridge', 'random_forest')
TOLERANCE = 1e-8
TARGET = 'synthetic_score'


def records(frame: pd.DataFrame) -> list[dict]:
    return json.loads(frame.to_json(orient='records', double_precision=15))


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_sha(path: Path) -> str:
    return hashlib.sha256(path.read_text(encoding='utf-8-sig').encode('utf-8')).hexdigest()


def validate_split(data: pd.DataFrame, train: pd.DataFrame, test: pd.DataFrame) -> pd.DataFrame:
    if len(data) != 2000 or len(train) != 1608 or len(test) != 392:
        raise ValueError('Ожидается 2000 строк: 1608 train и 392 test.')
    required = ['sample_id', *GROUP_FEATURES, TARGET, 'data_origin']
    if set(data.columns) != set(required) or set(train.columns) != {*required, 'configuration_id'} or set(test.columns) != set(train.columns):
        raise ValueError('Состав столбцов набора или разбиений изменён.')
    if any(frame.isna().any().any() or not frame.sample_id.is_unique for frame in (data,train,test)):
        raise ValueError('Пропуски или повтор sample_id.')
    if set(data.sample_id) != set(range(1,2001)) or set(train.sample_id) & set(test.sample_id):
        raise ValueError('Пересечение sample_id или неверный исходный состав.')
    joined = pd.concat([train,test]).sort_values('sample_id').reset_index(drop=True)
    try:
        pd.testing.assert_frame_equal(joined[data.columns], data.sort_values('sample_id').reset_index(drop=True), check_dtype=False, check_exact=True)
    except AssertionError as exc:
        raise ValueError('Содержание train/test не совпадает с исходным набором.') from exc
    numeric = data[[name for name in FEATURES if name!='storage_type'] + [TARGET]].to_numpy(float)
    if not np.isfinite(numeric).all() or (numeric<=0).any() or not data.data_origin.eq('synthetic').all():
        raise ValueError('Неверные числовые значения или происхождение данных.')
    if not data.storage_type.isin(['SSD','HDD']).all():
        raise ValueError('Неизвестный тип накопителя.')
    expected_groups = data.sort_values('sample_id').groupby(GROUP_FEATURES,sort=True).ngroup().to_numpy()
    if not np.array_equal(joined.configuration_id.to_numpy(),expected_groups) or joined.configuration_id.nunique()!=100:
        raise ValueError('configuration_id не соответствует 100 полным конфигурациям.')
    if set(train.configuration_id) & set(test.configuration_id) or train.configuration_id.nunique()!=80 or test.configuration_id.nunique()!=20:
        raise ValueError('Утечка конфигураций между train/test.')
    return joined


def load_bundle(root: Path = ROOT) -> dict:
    demo = load_payload(root)
    reference = json.loads((root/'data/demo/ml_reference.json').read_text(encoding='utf-8-sig'))
    data = pd.read_csv(root/'data/synthetic/laptops_synthetic.csv')
    train = pd.read_csv(root/'data/processed/train.csv')
    test = pd.read_csv(root/'data/processed/test.csv')
    joined = validate_split(data,train,test)
    if fingerprint(records(data.sort_values('sample_id')))!=reference['dataset_semantic_sha256']:
        raise ValueError('Исходный набор изменён относительно проверенного эталона.')
    train_ids = set(train.sample_id)
    splits = {int(row.sample_id): ('train' if int(row.sample_id) in train_ids else 'test') for row in joined.itertuples()}
    groups = [{'configuration_id':str(int(group)), 'split':str(part)} for group,part in
              sorted({(int(row.configuration_id),splits[int(row.sample_id)]) for row in joined.itertuples()})]
    cv_folds = []
    for number,(fit,valid) in enumerate(GroupKFold(5).split(train[FEATURES],train[TARGET],train.configuration_id),1):
        fit_groups = sorted(int(x) for x in train.iloc[fit].configuration_id.unique())
        valid_groups = sorted(int(x) for x in train.iloc[valid].configuration_id.unique())
        if set(fit_groups) & set(valid_groups):
            raise ValueError('Утечка конфигураций в CV.')
        cv_folds.append({'fold':number,'fit_configuration_ids':fit_groups,'validation_configuration_ids':valid_groups})
    manifest = {'method':'GroupShuffleSplit','seed':42,'test_size':0.2,
                'train_sample_ids':sorted(int(x) for x in train.sample_id),
                'test_sample_ids':sorted(int(x) for x in test.sample_id),
                'train_configuration_ids':sorted(int(x) for x in train.configuration_id.unique()),
                'test_configuration_ids':sorted(int(x) for x in test.configuration_id.unique()),
                'cv_method':'GroupKFold','cv_folds':cv_folds}
    cv_table = pd.read_csv(root/'results/model_comparison_cv.csv').set_index('model')
    metrics_table = pd.read_csv(root/'results/model_comparison_test.csv').set_index('model')
    saved_test = pd.read_csv(root/'results/test_predictions.csv').set_index('sample_id')
    if not cv_table.index.is_unique or set(cv_table.index)!=set(MODELS) or not metrics_table.index.is_unique or set(metrics_table.index)!=set(MODELS):
        raise ValueError('В результатах должны быть Ridge и Random Forest по одному разу.')
    if not np.isfinite(cv_table.cv_mae.to_numpy(float)).all() or (cv_table.cv_mae<0).any():
        raise ValueError('Некорректные CV-метрики.')
    selected = (root/'results/selected_model.txt').read_text(encoding='utf-8-sig').strip()
    if selected not in MODELS or selected != cv_table.cv_mae.idxmin():
        raise ValueError('Выбор модели не соответствует минимальной MAE CV.')
    if not saved_test.index.is_unique or set(saved_test.index)!=set(test.sample_id):
        raise ValueError('Сохранённые тестовые прогнозы имеют другой состав sample_id.')
    alternatives = pd.read_csv(root/'data/decision/alternatives.csv').set_index('alternative_id')
    known = set(data[FEATURES].itertuples(index=False,name=None))
    if not all(row in known for row in alternatives[FEATURES].itertuples(index=False,name=None)):
        raise ValueError('Альтернатива вне конфигураций учебного набора.')
    loaded_models, predictions = {}, {}
    for name in MODELS:
        artifact = (root/f'models/{name}.joblib').read_bytes()
        if hashlib.sha256(artifact).hexdigest()!=reference['artifacts'][name]:
            raise ValueError(f'{name}: контрольная сумма артефакта не совпала; файл не загружался.')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error',InconsistentVersionWarning)
                model = joblib.load(io.BytesIO(artifact))
        except InconsistentVersionWarning as exc:
            raise ValueError('Версия scikit-learn не соответствует сохранённой модели.') from exc
        expected_type = Ridge if name=='ridge' else RandomForestRegressor
        if not isinstance(model,Pipeline) or not isinstance(model.named_steps.get('model'),expected_type):
            raise ValueError('Сохранён неверный тип Pipeline/регрессора.')
        if list(model.feature_names_in_) != FEATURES:
            raise ValueError('Порядок признаков Pipeline изменён.')
        test_prediction = model.predict(test[FEATURES])
        actual_metrics = {'test_mae':float(mean_absolute_error(test[TARGET],test_prediction)),
                          'test_rmse':float(np.sqrt(mean_squared_error(test[TARGET],test_prediction))),
                          'test_r2':float(r2_score(test[TARGET],test_prediction))}
        if not np.isfinite(test_prediction).all() or not np.allclose(test_prediction,saved_test.loc[test.sample_id,f'{name}_prediction'].to_numpy(float),atol=TOLERANCE,rtol=0):
            raise ValueError(f'{name}: тестовые прогнозы не воспроизводятся.')
        for key,value in actual_metrics.items():
            if not np.isclose(value,float(metrics_table.loc[name,key]),atol=TOLERANCE,rtol=0):
                raise ValueError(f'{name}: метрика {key} не воспроизводится.')
        chosen_params = json.loads(cv_table.loc[name,'best_parameters'])
        full_params = model.get_params()
        if any(full_params.get(key)!=value for key,value in chosen_params.items()):
            raise ValueError(f'{name}: параметры модели не совпадают с выбором по CV.')
        predicted = model.predict(alternatives[FEATURES])
        if not np.isfinite(predicted).all() or (predicted<=0).any():
            raise ValueError('Прогнозы должны быть положительными и конечными.')
        predictions[name] = {str(code):float(value) for code,value in zip(alternatives.index,predicted)}
        loaded_models[name] = {'name':name,'version':1,'algorithm':name,'artifact_path':f'models/{name}.joblib',
                              'artifact_sha256':hashlib.sha256(artifact).hexdigest(),
                              'params':{'estimator':model.named_steps['model'].get_params(deep=False),
                                        'selected_parameters':chosen_params,'cv_scoring':'neg_mean_absolute_error'},
                              'metrics':{**{key:float(metrics_table.loc[name,key]) for key in actual_metrics},'cv_mae':float(cv_table.loc[name,'cv_mae'])}}
    saved_alternatives = pd.read_csv(root/'results/decision/alternatives_with_predictions.csv').set_index('alternative_id')
    if not saved_alternatives.index.is_unique or set(saved_alternatives.index)!=set(alternatives.index):
        raise ValueError('Состав сохранённых прогнозов альтернатив изменён.')
    if not np.allclose([predictions[selected][str(code)] for code in saved_alternatives.index],saved_alternatives.performance_score.to_numpy(float),atol=TOLERANCE,rtol=0):
        raise ValueError('Активная модель не воспроизводит сохранённые прогнозы пяти ноутбуков.')
    raw_records = records(data.sort_values('sample_id'))
    semantic_sha = fingerprint(raw_records)
    processed_records = records(joined)
    row_values=[]
    for row in raw_records:
        row_values.append({'layer':'raw','row_number':row['sample_id'],'configuration_id':None,'split':None,'data':row,
                           'provenance':{'source':'data/synthetic/laptops_synthetic.csv','sample_id':row['sample_id'],'origin':'synthetic'}})
    for row in processed_records:
        split=splits[row['sample_id']]
        row_values.append({'layer':'processed','row_number':row['sample_id'],'configuration_id':str(row['configuration_id']),'split':split,'data':row,
                           'provenance':{'source':f'data/processed/{split}.csv','sample_id':row['sample_id'],'origin':'synthetic','clean_reference':True}})
    row_values.sort(key=lambda row:(row['layer'],row['row_number']))
    generator_sha=code_sha(root/'scripts/generate_synthetic.py')
    generator_version='controlled-v1:'+generator_sha[:12]
    logical_manifest={'key':'ml-registry-variant27-v1','dataset_semantic_sha256':semantic_sha,
                      'generator_code_sha256':generator_sha,'training_code_sha256':code_sha(root/'scripts/train_models.py'),
                      'split_manifest':manifest,'models':loaded_models,'selected_model':selected,
                      'demo_fingerprint':fingerprint(demo),'features':FEATURES,'target':TARGET}
    if fingerprint(logical_manifest)!=reference['logical_fingerprint']:
        raise ValueError('Метаданные, разбиения или исходные файлы отличаются от проверенного эталона.')
    environment={'training_timestamp_known':False,'registration_source':'existing trusted project artifacts',
                 'verification_environment':{'python':platform.python_version(),**{key:importlib.metadata.version(key) for key in ('numpy','pandas','scikit-learn','scipy','joblib')}}}
    return {'fingerprint':fingerprint(logical_manifest),'logical_manifest':logical_manifest,'demo':demo,
            'rows':row_values,'groups':groups,'models':loaded_models,'predictions':predictions[selected],
            'environment':environment,'dataset':{'sha256':file_sha(root/'data/synthetic/laptops_synthetic.csv'),
                'generator_version':generator_version,'semantic_sha256':semantic_sha},'manifest':manifest,'selected':selected}


def check_base_project(connection, project: dict, bundle: dict) -> None:
    base = read_project(connection,project['id'])
    base['scores'] = [row for row in base['scores'] if row['criterion_code']!='performance_score']
    if fingerprint(base)!=fingerprint(bundle['demo']):
        raise ValueError('Исходный демонстрационный проект изменён. Его данные не перезаписаны.')


def validate_registered(connection, project_id: int, bundle: dict) -> dict:
    datasets=connection.execute('SELECT * FROM datasets WHERE project_id=%s',(project_id,)).fetchall()
    if len(datasets)!=1 or datasets[0]['generation_params'].get('semantic_sha256')!=bundle['dataset']['semantic_sha256']:
        raise ValueError('Реестр набора данных изменён.')
    dataset=datasets[0]
    if dataset['row_count']!=2000 or dataset['column_count']!=9 or dataset['origin']!='synthetic' or dataset['seed']!=42 or dataset['generator_version']!=bundle['dataset']['generator_version'] or dataset['generation_params'].get('generator_code_sha256')!=bundle['logical_manifest']['generator_code_sha256']:
        raise ValueError('Паспорт набора данных изменён.')
    actual_rows=[dict(row) for row in connection.execute('''SELECT layer::text AS layer,row_number,configuration_id,split::text AS split,data,provenance
        FROM dataset_rows WHERE dataset_id=%s ORDER BY layer::text,row_number''',(dataset['id'],)).fetchall()]
    if fingerprint(actual_rows)!=fingerprint(bundle['rows']):
        raise ValueError('Строки набора данных в БД отличаются от источника.')
    actual_groups=[dict(row) for row in connection.execute('SELECT configuration_id,split::text AS split FROM dataset_groups WHERE dataset_id=%s',(dataset['id'],)).fetchall()]
    if sorted(actual_groups,key=lambda row:int(row['configuration_id']))!=bundle['groups']:
        raise ValueError('Разбиение конфигураций в БД изменено.')
    actual_models=connection.execute('SELECT * FROM ml_models WHERE project_id=%s',(project_id,)).fetchall()
    if len(actual_models)!=2:
        raise ValueError('Ожидаются две зарегистрированные модели.')
    for model in actual_models:
        expected=bundle['models'].get(model['name'])
        if not expected or any(model[key]!=expected[key] for key in ('version','algorithm','artifact_path','artifact_sha256','params','metrics')):
            raise ValueError('Метаданные или версия модели изменены.')
        if model['dataset_id']!=dataset['id'] or model['feature_names']!=FEATURES or model['target_name']!=TARGET or model['seed']!=42 or model['split_manifest']!=bundle['manifest'] or model['trained_at'] is not None or model['is_active']!=(model['name']==bundle['selected']):
            raise ValueError('Происхождение, активная модель или разбиение изменены.')
    actual=connection.execute('''SELECT a.code,p.value,p.features_snapshot,p.model_id,m.name,m.is_active,s.value AS score_value,s.source::text AS source,s.prediction_id,p.id AS id
        FROM predictions p JOIN alternatives a ON a.id=p.alternative_id JOIN ml_models m ON m.id=p.model_id
        JOIN scores s ON s.project_id=p.project_id AND s.alternative_id=p.alternative_id AND s.criterion_id=p.criterion_id
        WHERE p.project_id=%s''',(project_id,)).fetchall()
    if len(actual)!=5 or {row['code'] for row in actual}!=set(bundle['predictions']):
        raise ValueError('Состав прогнозов изменён.')
    features={row['code']:{name:row['features'][name] for name in FEATURES} for row in bundle['demo']['alternatives']}
    for row in actual:
        if row['name']!=bundle['selected'] or not row['is_active'] or row['source']!='ml' or row['prediction_id']!=row['id'] or row['features_snapshot']!=features[row['code']] or not np.isclose(row['value'],bundle['predictions'][row['code']],rtol=0,atol=TOLERANCE) or row['score_value']!=row['value']:
            raise ValueError('Прогнозы или значения критериев изменены; ручные изменения сохранены.')
    total=connection.execute('SELECT count(*) AS n FROM scores WHERE project_id=%s',(project_id,)).fetchone()['n']
    if total!=25:
        raise ValueError('Матрица решений не содержит 25 оценок.')
    return {'project_id':project_id,'dataset_id':dataset['id'],'dataset_observations':2000,'stored_rows':4000,
            'configurations':100,'models':2,'active_model':bundle['selected'],'predictions':5,'scores':25}


def register(connection, bundle: dict) -> dict:
    """Write inside the caller's transaction; never overwrite an existing import."""
    connection.execute('SELECT pg_advisory_xact_lock(%s)',(LOCK_KEY,))
    version=connection.execute('SELECT version_num FROM alembic_version').fetchone()
    if not version or version['version_num']!='0002':
        raise ValueError('Для регистрации требуется миграция 0002.')
    projects=connection.execute("SELECT id,owner_id,revision,settings FROM projects WHERE settings->>'demo_key'=%s FOR UPDATE",(bundle['demo']['key'],)).fetchall()
    if len(projects)!=1:
        raise ValueError('Ожидается один ранее загруженный демонстрационный проект.')
    project=projects[0]
    check_base_project(connection,project,bundle)
    previous=project['settings'].get('ml_registry_fingerprint')
    if previous:
        if previous!=bundle['fingerprint']:
            raise ValueError('Файлы ML или исходные данные изменены. Существующий реестр не перезаписан.')
        result=validate_registered(connection,project['id'],bundle)
        return dict(result,created=False)
    if project['revision']!=project['settings'].get('demo_revision'):
        raise ValueError('Проект изменён после начальной загрузки; автоматическая регистрация остановлена.')
    for table in ('datasets','ml_models','predictions'):
        # Identifiers are fixed constants, not user input.
        from psycopg import sql
        if connection.execute(sql.SQL('SELECT count(*) AS n FROM {} WHERE project_id=%s').format(sql.Identifier(table)),(project['id'],)).fetchone()['n']:
            raise ValueError('В проекте уже есть ML-записи без отметки этого импорта.')
    project_id,actor_id=project['id'],project['owner_id']
    dataset_id=connection.execute('''INSERT INTO datasets(project_id,name,origin,license,sha256,seed,generator_version,generation_params,quality_report,row_count,column_count,uploaded_by)
        VALUES(%s,%s,'synthetic',%s,%s,42,%s,%s,%s,2000,9,%s) RETURNING id''',
        (project_id,'laptops_synthetic, clean reference v1','Авторская учебная синтетика; внешняя лицензия не заявляется.',bundle['dataset']['sha256'],bundle['dataset']['generator_version'],
         Jsonb({'semantic_sha256':bundle['dataset']['semantic_sha256'],'generator_code_sha256':bundle['logical_manifest']['generator_code_sha256'],
                'noise':{'distribution':'lognormal','log_mean':-0.00125,'log_std':0.05},'seed':42,'n_rows':2000,'defects':{'missing':0,'outliers_injected':0,'duplicates_injected':0}}),
         Jsonb({'observations':2000,'configurations':100,'missing':0,'duplicate_sample_ids':0,'train_rows':1608,'test_rows':392,'train_groups':80,'test_groups':20,'split_overlap':0,'origin':'synthetic','real_world_accuracy_established':False}),actor_id)).fetchone()['id']
    connection.execute("""INSERT INTO dataset_groups(dataset_id,configuration_id,split)
        SELECT %s,x.configuration_id,x.split::data_split
        FROM jsonb_to_recordset(%s::jsonb) AS x(configuration_id text,split text)""",
        (dataset_id,Jsonb(bundle['groups'])))
    # Set-based import: one statement per 500 rows, no per-observation INSERT loop.
    for start in range(0,len(bundle['rows']),500):
        connection.execute("""INSERT INTO dataset_rows(dataset_id,layer,row_number,configuration_id,split,data,provenance)
            SELECT %s,x.layer::data_layer,x.row_number,x.configuration_id,x.split::data_split,x.data,x.provenance
            FROM jsonb_to_recordset(%s::jsonb) AS x(layer text,row_number integer,configuration_id text,split text,data jsonb,provenance jsonb)""",
            (dataset_id,Jsonb(bundle['rows'][start:start+500])))
    model_ids={}
    for name in MODELS:
        model=bundle['models'][name]
        model_ids[name]=connection.execute('''INSERT INTO ml_models(project_id,dataset_id,name,version,algorithm,feature_names,target_name,params,metrics,environment,split_manifest,seed,artifact_path,artifact_sha256,is_active,trained_at,created_by)
            VALUES(%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,42,%s,%s,%s,NULL,%s) RETURNING id''',
            (project_id,dataset_id,name,name,Jsonb(FEATURES),TARGET,Jsonb(model['params']),Jsonb(model['metrics']),Jsonb(bundle['environment']),Jsonb(bundle['manifest']),model['artifact_path'],model['artifact_sha256'],name==bundle['selected'],actor_id)).fetchone()['id']
    criterion=connection.execute("SELECT id FROM criteria WHERE project_id=%s AND code='performance_score' AND source='ml'",(project_id,)).fetchone()
    if not criterion:
        raise ValueError('Не найден ML-критерий производительности.')
    alternatives={row['code']:row for row in connection.execute('SELECT id,code,features FROM alternatives WHERE project_id=%s',(project_id,)).fetchall()}
    for code,value in bundle['predictions'].items():
        alternative=alternatives[code]
        snapshot={name:alternative['features'][name] for name in FEATURES}
        prediction=connection.execute('''INSERT INTO predictions(project_id,model_id,alternative_id,criterion_id,value,features_snapshot,created_by)
            VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id''',(project_id,model_ids[bundle['selected']],alternative['id'],criterion['id'],value,Jsonb(snapshot),actor_id)).fetchone()['id']
        connection.execute('''INSERT INTO scores(project_id,alternative_id,criterion_id,expert_id,value,source,prediction_id)
            VALUES(%s,%s,%s,%s,%s,'ml',%s)''',(project_id,alternative['id'],criterion['id'],actor_id,value,prediction))
    connection.execute('''UPDATE projects SET settings=settings || %s::jsonb WHERE id=%s''',
        (Jsonb({'ml_registry_fingerprint':bundle['fingerprint'],'ml_predictions_pending':False,'ml_registry_version':1}),project_id))
    connection.execute('''INSERT INTO audit_events(project_id,actor_id,action,entity_type,entity_id,details)
        VALUES(%s,%s,'register_ml','project',%s,%s)''',(project_id,actor_id,str(project_id),Jsonb({'fingerprint':bundle['fingerprint'],'dataset_id':dataset_id,'models':model_ids,'selected_by':'minimum CV MAE','database_role':connection.execute('SELECT current_user AS role').fetchone()['role']})))
    result=validate_registered(connection,project_id,bundle)
    return dict(result,created=True)


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-files',action='store_true')
    parser.add_argument('--upgrade',action='store_true',help='Apply Alembic 0002 before registering')
    parser.add_argument('--host',default='localhost')
    parser.add_argument('--port',type=int,default=5432)
    args=parser.parse_args()
    print('Проверка существующих данных и моделей...',flush=True)
    bundle=load_bundle()
    if args.check_files:
        print('Проверено: 2000 наблюдений, 100 конфигураций, Ridge и Random Forest; тестовые метрики и 5 прогнозов воспроизводятся.')
        print('Выбор по CV:',bundle['selected'])
        print('Отпечаток реестра:',bundle['fingerprint'])
        print('Подключение к БД не выполнялось. Переобучения не было.')
        return
    password=getpass.getpass('Пароль coursework_migrator: ')
    # A session lock serializes the optional migration and the import across CLI runs.
    conninfo=make_conninfo(host=args.host,port=args.port,dbname='coursework_laptop',user='coursework_migrator',password=password,connect_timeout=10)
    with psycopg.connect(conninfo,autocommit=True,row_factory=dict_row) as connection:
        connection.execute('SELECT pg_advisory_lock(%s)',(LOCK_KEY,))
        if args.upgrade:
            from alembic import command
            from alembic.config import Config
            from sqlalchemy import URL
            previous=os.environ.get('DATABASE_URL')
            try:
                os.environ['DATABASE_URL']=URL.create('postgresql+psycopg',username='coursework_migrator',password=password,host=args.host,port=args.port,database='coursework_laptop').render_as_string(hide_password=False)
                command.upgrade(Config(str(ROOT/'alembic.ini')),'0002')
            finally:
                if previous is None:
                    os.environ.pop('DATABASE_URL',None)
                else:
                    os.environ['DATABASE_URL']=previous
        with connection.transaction():
            result=register(connection,bundle)
    print('ML-реестр и прогнозы загружены.' if result['created'] else 'ML-реестр уже существует; дубликаты не созданы.')
    print(json.dumps(result,ensure_ascii=False))
    print('Матрица проекта заполнена: 5 альтернатив × 5 критериев. Прогнозы относятся только к синтетическому эксперименту.')


if __name__=='__main__':
    try:
        main()
    except (ValueError,OSError,psycopg.Error) as exc:
        if isinstance(exc,psycopg.Error):
            print(f'Ошибка PostgreSQL ({exc.sqlstate or "подключение"}). Импорт данных не сохранён.',file=sys.stderr)
            if exc.diag.message_primary:
                print(exc.diag.message_primary,file=sys.stderr)
        else:
            print(f'Ошибка: {exc}',file=sys.stderr)
        raise SystemExit(1)
