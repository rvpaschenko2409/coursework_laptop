"""Preview rankings from a saved calculation."""
from inspect import getsource
from pathlib import Path

import pandas as pd
from fastapi import Form, HTTPException, Request
from fastapi.responses import JSONResponse
from decision_core.marcos import calculate_marcos
from decision_core.vikor import calculate_vikor
from decision_store.calculations import matrix_from_snapshot, ranking, plain
from web_app.calculation_store import load_pair
from web_app.security import csrf_check
from web_app.sensitivity_math import compare, perturb


def register_routes(app, Ctx, project_access):
    @app.get('/api/projects/{project_id}/calculations/{result_id}/preview')
    def browser_input(project_id: int, result_id: int, context: Ctx):
        project_access(context, project_id)
        data = load_pair(context.connection, project_id, result_id)
        if data is None:
            raise HTTPException(404, 'Исходный расчёт не найден.')
        matrix, _ = matrix_from_snapshot(data['snapshot'])
        core = Path(__file__).resolve().parents[1] / 'decision_core'
        files = ('__init__.py', 'criteria.py', 'normalization.py', 'marcos.py', 'vikor.py')
        payload = {
            'sources': {name: (core / name).read_text(encoding='utf-8-sig') for name in files},
            'matrix': matrix.to_dict(orient='split'),
            'weights': {row['criterion']: row['weight'] for row in data['weighting']['weights']},
            'rankings': {
                method: data['calculated'][method]['ranking']
                for method in ('marcos', 'vikor')
            },
        }
        payload['sources']['preview.py'] = (
            'import numpy as np\n\n' + getsource(perturb) + '\n\n' + getsource(compare)
        )
        return JSONResponse(plain(payload), headers={'Cache-Control': 'no-store'})

    @app.post('/api/projects/{project_id}/calculations/{result_id}/preview')
    def preview(project_id: int, result_id: int, request: Request, context: Ctx,
                criterion: str = Form(), change: int = Form(ge=-50, le=50),
                v: float = Form(ge=0, le=1), csrf: str = Form()):
        project_access(context, project_id)
        csrf_check(context, csrf, request)
        try:
            data = load_pair(context.connection, project_id, result_id)
            if data is None:
                raise HTTPException(404, 'Исходный расчёт не найден.')
            matrix, _ = matrix_from_snapshot(data['snapshot'])
            base = {row['criterion']: row['weight'] for row in data['weighting']['weights']}
            weights = pd.Series(perturb(base, criterion, change / 100))
            marcos = ranking(calculate_marcos(matrix, weights))
            vikor, conditions = calculate_vikor(matrix, weights, v=v)
            vikor = ranking(vikor)
            return plain({
                'weights': weights.to_dict(),
                'marcos': marcos,
                'vikor': vikor,
                'conditions': conditions,
                'changes': {
                    'marcos': compare(marcos, data['calculated']['marcos']['ranking']),
                    'vikor': compare(vikor, data['calculated']['vikor']['ranking']),
                },
            })
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
