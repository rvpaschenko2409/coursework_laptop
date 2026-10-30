"""Predict only with registered, checksum-verified local coursework artifacts."""
from functools import lru_cache
from pathlib import Path
import hashlib
import io
import math
import warnings
import joblib
import pandas as pd
from sklearn.exceptions import InconsistentVersionWarning
from sklearn.pipeline import Pipeline
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from web_app.alternative_validation import FEATURES

ROOT=Path(__file__).resolve().parents[1]


@lru_cache(maxsize=4)
def load_pipeline(root,relative,expected_hash,algorithm):
    allowed={'random_forest':'models/random_forest.joblib','ridge':'models/ridge.joblib'}
    if relative!=allowed.get(algorithm):raise ValueError('Путь зарегистрированной модели не поддерживается.')
    path=Path(root)/relative
    # Read and validate the exact bytes that will be deserialized.
    try:artifact=path.read_bytes()
    except OSError:raise ValueError('Файл зарегистрированной модели не найден.') from None
    if hashlib.sha256(artifact).hexdigest()!=expected_hash:raise ValueError('Контрольная сумма модели не совпала. Сохранение отменено.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',InconsistentVersionWarning)
            pipeline=joblib.load(io.BytesIO(artifact))
    except Exception:raise ValueError('Модель не загружена. Проверьте версии библиотек и артефакт.') from None
    kind=RandomForestRegressor if algorithm=='random_forest' else Ridge
    if not isinstance(pipeline,Pipeline) or not isinstance(pipeline.named_steps.get('model'),kind) or list(pipeline.feature_names_in_)!=FEATURES:
        raise ValueError('Состав признаков или тип Pipeline не соответствует реестру.')
    return pipeline


def active_model(connection,project_id):
    return connection.execute('''SELECT m.id,m.algorithm,m.artifact_path,m.artifact_sha256,m.feature_names,d.origin
        FROM ml_models m JOIN datasets d ON d.project_id=m.project_id AND d.id=m.dataset_id
        WHERE m.project_id=%s AND m.is_active''',(project_id,)).fetchone()


def predict(model,features):
    if model['feature_names']!=FEATURES:raise ValueError('Состав признаков активной модели не поддерживается.')
    pipeline=load_pipeline(str(ROOT),model['artifact_path'],model['artifact_sha256'],model['algorithm'])
    value=float(pipeline.predict(pd.DataFrame([{key:features[key] for key in FEATURES}],columns=FEATURES))[0])
    if not math.isfinite(value) or value<=0:raise ValueError('Модель вернула недопустимый прогноз. Сохранение отменено.')
    return value
