"""Group-isolated evaluation: tuning and selection never use held-out test scores."""
import importlib.metadata
import platform
import hashlib
from pathlib import Path
import time

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error,mean_squared_error,r2_score
from sklearn.model_selection import GridSearchCV,GroupKFold,GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder,StandardScaler
from threadpoolctl import threadpool_limits

from web_app.alternative_validation import FEATURES,HARDWARE,number
from web_app.training_data_store import digest

VERSION='web-model-training-v1'
GROUP_FEATURES=['cpu_cores','cpu_threads','cpu_frequency_ghz','ram_gb','storage_type','storage_gb']


def validate_records(records):
    if not 20<=len(records)<=10000:raise ValueError('Нужно от 20 до 10000 проверенных наблюдений.')
    output=[]
    for row in records:
        values={key:number(str(row['data'].get(key,'')),*args) for key,args in HARDWARE.items()}
        if values['cpu_threads']<values['cpu_cores']:raise ValueError('В данных потоков меньше, чем ядер.')
        values['storage_type']=row['data'].get('storage_type')
        if values['storage_type'] not in ('SSD','HDD'):raise ValueError('В данных недопустимый тип накопителя.')
        target=row['data'].get('performance_score',row['data'].get('synthetic_score'))
        values['performance_score']=number(str(target),'Целевой балл',0.000001,1e9)
        output.append({'row_number':row['row_number'],'data':values})
    frame=pd.DataFrame([r['data'] for r in output])
    if frame.performance_score.nunique()<2:raise ValueError('Целевая переменная постоянна: сравнение регрессоров неинформативно.')
    groups=frame.groupby(GROUP_FEATURES,sort=True).ngroup().to_numpy()
    if len(set(groups))<10:raise ValueError('Нужно не меньше 10 различных аппаратных конфигураций.')
    return output,frame,groups


def split(records,seed):
    rows,frame,groups=validate_records(records)
    train,test=next(GroupShuffleSplit(n_splits=1,test_size=.2,random_state=seed).split(frame[FEATURES],groups=groups))
    cv=list(GroupKFold(n_splits=5).split(frame.iloc[train][FEATURES],frame.iloc[train].performance_score,groups[train]))
    if len(test)<2 or frame.iloc[train].performance_score.nunique()<2:raise ValueError('После разделения недостаточно наблюдений или вариации целевого балла.')
    train_groups=set(map(int,groups[train]));test_groups=set(map(int,groups[test]))
    assert not train_groups&test_groups
    folds=[]
    for number_,(fit,valid) in enumerate(cv,1):
        fg=set(map(int,groups[train][fit]));vg=set(map(int,groups[train][valid]))
        assert not fg&vg
        folds.append({'fold':number_,'train_groups':sorted(fg),'valid_groups':sorted(vg),
                      'train_rows':len(fit),'valid_rows':len(valid),'group_overlap':0})
    manifest={'version':VERSION,'seed':seed,'method':'GroupShuffleSplit','test_group_fraction':.2,
        'groups_derived_from':GROUP_FEATURES,'train_groups':sorted(train_groups),'test_groups':sorted(test_groups),
        'train_rows':len(train),'test_rows':len(test),'group_overlap':0,'folds':folds,
        'assignments':[{'row_number':r['row_number'],'configuration_id':str(int(groups[i])),
                        'split':'train' if int(groups[i]) in train_groups else 'test'} for i,r in enumerate(rows)]}
    return rows,frame,groups,train,test,cv,manifest


def train(records,seed,progress):
    rows,frame,groups,train_indices,test_indices,cv,manifest=split(records,seed)
    features=frame[FEATURES];target=frame.performance_score
    specs={
        'ridge':(Ridge(),{'model__alpha':[.1,1.0,10.0]}),
        'random_forest':(RandomForestRegressor(n_estimators=200,random_state=seed,n_jobs=1),
                         {'model__max_depth':[5,None],'model__min_samples_leaf':[1,5]}),
    }
    models={};started=time.perf_counter()
    with threadpool_limits(limits=1):
        for name,(estimator,grid) in specs.items():
            progress('Кросс-валидация: '+('Ridge' if name=='ridge' else 'Random Forest'))
            preprocessing=ColumnTransformer([
                ('numeric',StandardScaler(),[k for k in FEATURES if k!='storage_type']),
                ('categorical',OneHotEncoder(handle_unknown='ignore',sparse_output=False),['storage_type'])])
            pipeline=Pipeline([('preprocessing',preprocessing),('model',estimator)])
            search=GridSearchCV(pipeline,grid,scoring='neg_mean_absolute_error',cv=cv,n_jobs=1,refit=True,error_score='raise')
            search.fit(features.iloc[train_indices],target.iloc[train_indices])
            metrics={'cv_mae':float(-search.best_score_)}
            candidates=[{'params':p,'mean_cv_mae':float(-mean),'std_cv_mae':float(std)}
                for p,mean,std in zip(search.cv_results_['params'],search.cv_results_['mean_test_score'],search.cv_results_['std_test_score'])]
            models[name]={'pipeline':search.best_estimator_,'params':search.best_params_,'metrics':metrics,'candidates':candidates}
        selected=min(models,key=lambda name:(models[name]['metrics']['cv_mae'],name))
        # The recommendation is now fixed. Only then evaluate each fitted model once on the holdout.
        progress('Итоговая оценка на отложенном тесте')
        for name,model in models.items():
            predicted=model['pipeline'].predict(features.iloc[test_indices])
            if not np.isfinite(predicted).all():raise ValueError('Модель вернула неконечные прогнозы на тесте.')
            actual=target.iloc[test_indices]
            model['metrics'].update(test_mae=float(mean_absolute_error(actual,predicted)),
                test_rmse=float(np.sqrt(mean_squared_error(actual,predicted))),
                test_r2=float(r2_score(actual,predicted)) if actual.nunique()>1 else None)
    return {'models':models,'selected':selected,'manifest':manifest,'rows':rows,
        'elapsed_seconds':float(time.perf_counter()-started),
        'environment':{'python':platform.python_version(),'packages':{k:importlib.metadata.version(k)
                        for k in ('numpy','pandas','scikit-learn','scipy','joblib')},
                       'training_version':VERSION,'training_code_sha256':hashlib.sha256(Path(__file__).read_text(encoding='utf-8').encode('utf-8')).hexdigest(),'selection':'minimum mean 5-fold group CV MAE; test excluded',
                       'training_timestamp_known':True},
        'input_sha256':digest(records)}
