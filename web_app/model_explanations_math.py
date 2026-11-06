"""Held-out permutation importance and additive SHAP for a saved prediction."""
import hashlib
import importlib.metadata
import math
from pathlib import Path

import numpy as np
import pandas as pd
import shap
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error
from threadpoolctl import threadpool_limits

from web_app.alternative_validation import FEATURES
from web_app.training_data_store import digest

VERSION='web-model-explanations-v1'
REPEATS=30
SEED=42
MAX_TEST=500
MAX_BACKGROUND=100
LABELS={'cpu_cores':'Ядра процессора','cpu_threads':'Потоки процессора','cpu_frequency_ghz':'Частота, ГГц',
        'ram_gb':'Оперативная память, ГБ','storage_gb':'Накопитель, ГБ','storage_type':'Тип накопителя'}


def producer():
    return {'version':VERSION,'code_sha256':hashlib.sha256(Path(__file__).read_text(encoding='utf-8').encode('utf-8')).hexdigest(),
            'packages':{k:importlib.metadata.version(k) for k in ('numpy','pandas','scikit-learn','shap','joblib')},
            'permutation_repeats':REPEATS,'seed':SEED,'test_sample_limit':MAX_TEST,'background_limit':MAX_BACKGROUND}


def frame(rows):
    return pd.DataFrame([{key:r['data'][key] for key in FEATURES} for r in rows],columns=FEATURES)


def subset(rows,limit):
    indices=np.sort(np.random.default_rng(SEED).choice(len(rows),min(len(rows),limit),replace=False))
    return [rows[int(i)] for i in indices]


def importance(pipeline,test):
    sample=subset(test,MAX_TEST)
    x=frame(sample);y=np.array([r['data']['performance_score'] for r in sample],dtype=float)
    with threadpool_limits(limits=1):
        baseline=float(mean_absolute_error(y,pipeline.predict(x)))
        result=permutation_importance(pipeline,x,y,scoring='neg_mean_absolute_error',n_repeats=REPEATS,random_state=SEED,n_jobs=1)
    rows=[{'feature':key,'label':LABELS[key],'mean':float(result.importances_mean[i]),
           'std':float(result.importances_std[i]),'repeats':[float(v) for v in result.importances[i]]} for i,key in enumerate(FEATURES)]
    if not all(math.isfinite(r['mean']) and math.isfinite(r['std']) for r in rows):raise ValueError('Важность признаков содержит неконечные значения.')
    return {'method':'permutation / increase in MAE','baseline_mae':baseline,'rows':sorted(rows,key=lambda r:r['mean'],reverse=True),
            'test_rows_total':len(test),'test_rows_used':len(sample),'sample_row_numbers':[r['row_number'] for r in sample],
            'repeats':REPEATS,'seed':SEED,'scoring':'neg_mean_absolute_error','subsampled':len(sample)<len(test)}


def local(pipeline,algorithm,features,train,expected):
    x=pd.DataFrame([{key:features[key] for key in FEATURES}],columns=FEATURES)
    transform=pipeline.named_steps['preprocessing'];estimator=pipeline.named_steps['model']
    numeric=[key for key in FEATURES if key!='storage_type']
    names=list(transform.get_feature_names_out())
    if names[:len(numeric)]!=['numeric__'+key for key in numeric] or any(not n.startswith('categorical__storage_type_') for n in names[len(numeric):]):
        raise ValueError('Структура преобразованных признаков модели не поддерживается для SHAP.')
    transformed=np.asarray(transform.transform(x),dtype=float)
    with threadpool_limits(limits=1):
        if algorithm=='random_forest':
            explainer=shap.TreeExplainer(estimator,feature_perturbation='tree_path_dependent',model_output='raw')
            explanation=explainer(transformed,check_additivity=True)
            mode='TreeExplainer / tree_path_dependent'
            background=[]
        elif algorithm=='ridge':
            background=subset(train,MAX_BACKGROUND)
            matrix=np.asarray(transform.transform(frame(background)),dtype=float)
            masker=shap.maskers.Independent(matrix,max_samples=MAX_BACKGROUND)
            explainer=shap.LinearExplainer(estimator,masker)
            explanation=explainer(transformed)
            mode='LinearExplainer / independent training background'
        else:raise ValueError('SHAP поддерживается только для Ridge и Random Forest.')
        prediction=float(pipeline.predict(x)[0])
    values=np.asarray(explanation.values,dtype=float).reshape(-1)
    base=float(np.asarray(explanation.base_values).reshape(-1)[0])
    if len(values)!=len(names):raise ValueError('Размер SHAP не соответствует преобразованию признаков.')
    contributions=[float(v) for v in values[:len(numeric)]]+[float(values[len(numeric):].sum())]
    reconstructed=float(base+sum(contributions));difference=abs(prediction-reconstructed)
    if not all(math.isfinite(v) for v in [base,prediction,reconstructed,*contributions]):raise ValueError('Объяснение содержит неконечные значения.')
    if not np.isclose(prediction,float(expected),atol=1e-6,rtol=1e-8):raise ValueError('Сохранённый прогноз не воспроизводится этой версией модели. Объяснение не сохранено.')
    if not np.isclose(prediction,reconstructed,atol=1e-6,rtol=1e-8):raise ValueError('Базовое значение и сумма SHAP-вкладов не совпали с прогнозом.')
    rows=[{'feature':key,'label':LABELS[key],'value':features[key],'contribution':value} for key,value in zip(FEATURES,contributions)]
    return {'method':mode,'base_value':base,'prediction':prediction,'saved_prediction':float(expected),
            'reconstructed_prediction':reconstructed,'additivity_error':difference,'atol':1e-6,'rtol':1e-8,
            'rows':rows,'background_rows':len(background),'background_row_numbers':[r['row_number'] for r in background],
            'categorical_grouping':'SHAP values of all one-hot storage indicators summed into storage_type'}
