"""Проверка согласованности переданного проекта без переобучения моделей."""
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from scipy.stats import chisquare
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold

from decision_core.dematel import calculate_dematel
from decision_core.marcos import calculate_marcos
from decision_core.merec import calculate_merec
from decision_core.vikor import calculate_vikor

ROOT = Path(__file__).resolve().parents[1]
FEATURES = ['cpu_cores', 'cpu_threads', 'cpu_frequency_ghz', 'ram_gb', 'storage_gb', 'storage_type']


def audit():
    checks = []

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append({'check': name, 'passed': True})

    data_path = ROOT / 'data/synthetic/laptops_synthetic.csv'
    data = pd.read_csv(data_path)
    train = pd.read_csv(ROOT / 'data/processed/train.csv')
    test = pd.read_csv(ROOT / 'data/processed/test.csv')
    check('2000 строк, заполненные значения, уникальные sample_id',
          len(data) == 2000 and not data.isna().any().any() and data.sample_id.is_unique)
    check('Положительная цель и маркировка происхождения',
          (data.synthetic_score > 0).all() and data.data_origin.eq('synthetic').all())
    check('100 уникальных полных конфигураций', len(data[FEATURES].drop_duplicates()) == 100)
    profiles = {(2, 4, 2.0), (4, 4, 2.5), (4, 8, 2.5), (6, 12, 2.8), (8, 16, 3.0)}
    check('Только объявленные профили процессора',
          set(data[['cpu_cores', 'cpu_threads', 'cpu_frequency_ghz']].itertuples(index=False, name=None)) == profiles)
    check('Допустимые RAM, тип и ёмкость накопителя',
          data.ram_gb.isin([4, 8, 16, 32]).all()
          and data.storage_type.isin(['SSD', 'HDD']).all()
          and data.loc[data.storage_type.eq('SSD'), 'storage_gb'].isin([256, 512, 1024]).all()
          and data.loc[data.storage_type.eq('HDD'), 'storage_gb'].isin([500, 1000]).all())
    train_config = set(train[FEATURES].itertuples(index=False, name=None))
    test_config = set(test[FEATURES].itertuples(index=False, name=None))
    check('Нет пересечения конфигураций train/test', train_config.isdisjoint(test_config))
    check('Все исходные строки входят в train или test ровно один раз',
          len(train) == 1608 and len(test) == 392
          and set(train.sample_id).isdisjoint(test.sample_id)
          and set(train.sample_id) | set(test.sample_id) == set(data.sample_id))
    restored = pd.concat([train, test]).sort_values('sample_id')[data.columns].reset_index(drop=True)
    pd.testing.assert_frame_equal(restored, data.sort_values('sample_id').reset_index(drop=True))
    check('Содержание разбиений совпадает с исходными строками', True)
    for fit, valid in GroupKFold(5).split(train[FEATURES], train.synthetic_score, train.configuration_id):
        check('Нет пересечения конфигураций в очередном блоке CV',
              set(train.iloc[fit].configuration_id).isdisjoint(train.iloc[valid].configuration_id))
    # Генератор запускается в изолированной временной папке.
    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        (temp / 'scripts').mkdir()
        shutil.copy2(ROOT / 'scripts/generate_synthetic.py', temp / 'scripts/generate_synthetic.py')
        subprocess.run([sys.executable, '-X', 'utf8', str(temp / 'scripts/generate_synthetic.py')],
                       check=True, capture_output=True, encoding='utf-8')
        regenerated = pd.read_csv(temp / 'data/synthetic/laptops_synthetic.csv')
        pd.testing.assert_frame_equal(regenerated, data, check_exact=True)
    check('Генератор с seed=42 точно воспроизводит значения всех строк', True)
    # Независимая проверка формулы и параметров мультипликативного шума.
    cpu = (data.cpu_cores / 4) ** .7 * (data.cpu_frequency_ghz / 2.5)
    ram = (data.ram_gb / 8) ** .3
    disk = np.where(data.storage_type.eq('SSD'), 1., .4)
    noiseless = 1000 / (.7 / cpu + .2 / ram + .1 / disk)
    log_noise = np.log(data.synthetic_score / noiseless)
    distribution_tests = []
    for feature in ['ram_gb', 'storage_type']:
        counts = data[feature].value_counts().sort_index()
        chi, p = chisquare(counts)
        distribution_tests.append({'feature': feature, 'chi2': float(chi), 'p': float(p),
                                   'total_variation_from_uniform': float(np.abs(counts / counts.sum() - 1 / len(counts)).sum() / 2)})
    saved_predictions = pd.read_csv(ROOT / 'results/test_predictions.csv').set_index('sample_id').loc[test.sample_id]
    saved_metrics = pd.read_csv(ROOT / 'results/model_comparison_test.csv').set_index('model')
    max_errors = {}
    for name in ['ridge', 'random_forest']:
        model = joblib.load(ROOT / 'models' / f'{name}.joblib')
        predicted = model.predict(test[FEATURES])
        error = np.max(np.abs(predicted - saved_predictions[f'{name}_prediction'].to_numpy()))
        max_errors[name] = float(error)
        check(f'{name}: сохранённый прогноз воспроизводится', error < 1e-8)
        metrics = [mean_absolute_error(test.synthetic_score, predicted),
                   np.sqrt(mean_squared_error(test.synthetic_score, predicted)),
                   r2_score(test.synthetic_score, predicted)]
        np.testing.assert_allclose(metrics, saved_metrics.loc[name, ['test_mae', 'test_rmse', 'test_r2']].to_numpy(float), rtol=0, atol=1e-8)
        check(f'{name}: сохранённые тестовые метрики воспроизводятся', True)
    cv = pd.read_csv(ROOT / 'results/model_comparison_cv.csv').sort_values('cv_mae')
    selected = (ROOT / 'results/selected_model.txt').read_text().strip()
    check('Модель выбрана по CV, таблица и selected_model.txt согласованы', selected == cv.iloc[0]['model'])
    matrix = pd.read_csv(ROOT / 'results/decision/decision_matrix.csv', index_col=0)
    original_merec = pd.read_csv(ROOT / 'results/decision/merec_weights.csv').set_index('criterion')
    merec = calculate_merec(matrix).set_index('criterion')
    np.testing.assert_allclose(merec.loc[original_merec.index], original_merec, rtol=0, atol=1e-12)
    check('MEREC совпадает с сохранёнными эффектами и весами', True)
    for name, actual in [('marcos', calculate_marcos(matrix, merec.weight)),
                         ('vikor', calculate_vikor(matrix, merec.weight)[0])]:
        saved = pd.read_csv(ROOT / f'results/decision/{name}_ranking.csv', index_col=0)
        pd.testing.assert_frame_equal(actual, saved.loc[actual.index, actual.columns], check_dtype=False, rtol=0, atol=1e-12)
        check(f'{name}: рейтинг и значения совпадают', True)
    direct = pd.read_csv(ROOT / 'data/decision/dematel_direct.csv', index_col=0)
    _, _, indicators = calculate_dematel(direct)
    saved = pd.read_csv(ROOT / 'results/dematel/indicators.csv', index_col=0)
    pd.testing.assert_frame_equal(indicators, saved, check_dtype=False, rtol=0, atol=1e-12)
    check('DEMATEL совпадает с сохранённым контрольным расчётом', True)
    shap_table = pd.read_csv(ROOT / 'results/local_shap.csv')
    shap_summary = json.loads((ROOT / 'results/local_shap_summary.json').read_text())
    add_error = abs(shap_summary['base_value'] + shap_table.shap_contribution.sum() - shap_summary['prediction'])
    check('Сохранённые SHAP-вклады воспроизводят сохранённый прогноз', add_error < 1e-6)
    summary = {
        'checks': checks, 'checked': len(checks),
        'runtime': {'python': platform.python_version(), 'numpy': np.__version__, 'pandas': pd.__version__, 'sklearn': sklearn.__version__},
        'sha256_raw_bytes': hashlib.sha256(data_path.read_bytes()).hexdigest(),
        'dataset': {'n': len(data), 'full_configurations': len(train_config | test_config),
                    'train_rows': len(train), 'test_rows': len(test), 'train_configurations': len(train_config), 'test_configurations': len(test_config)},
        'log_noise': {'sample_mean': float(log_noise.mean()), 'specified_mean': -.00125,
                      'sample_std': float(log_noise.std(ddof=1)), 'specified_std': .05,
                      'note': 'Небольшое расхождение включает выборочную вариацию и округление цели до 0.01.'},
        'uniform_distribution_diagnostics': distribution_tests,
        'prediction_max_errors': max_errors, 'saved_shap_additivity_error': float(add_error),
        'limitations': ['Коррелированные признаки не являются независимыми причинными факторами.',
                       'Контрфактическое перемешивание может нарушить допустимые CPU-профили.',
                       'Реальная точность, TSTR и эмпирическое рыночное правдоподобие не установлены.']}
    output = ROOT / 'results/audit_project.json'
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Успешно проверено условий: {len(checks)}')
    print(json.dumps(summary['log_noise'], ensure_ascii=False))
    print('Отчёт:', output)
    return summary


if __name__ == '__main__':
    audit()
