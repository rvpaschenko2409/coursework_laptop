"""Preview rankings from a saved calculation."""
from inspect import getsource
from pathlib import Path

import pandas as pd
from fastapi import Form, HTTPException, Request
from fastapi.responses import JSONResponse
from decision_core.criteria import CRITERIA
from decision_core.merec import calculate_merec
from decision_core.marcos import calculate_marcos
from decision_core.vikor import calculate_vikor
from decision_store.calculations import load_snapshot, matrix_from_snapshot, ranking, plain
from web_app import weight_store
from web_app.alternative_store import check_revision
from web_app.calculation_store import load_pair, parse_v, run_for_user
from web_app.security import csrf_check
from web_app.sensitivity_math import compare, perturb


def browser_sources():
    core = Path(__file__).resolve().parents[1] / 'decision_core'
    files = ('__init__.py', 'criteria.py', 'normalization.py', 'merec.py', 'marcos.py', 'vikor.py')
    sources = {name: (core / name).read_text(encoding='utf-8-sig') for name in files}
    sources['preview.py'] = 'import numpy as np\n\n' + getsource(perturb) + '\n\n' + getsource(compare)
    return sources


def register_routes(app, Ctx, project_access):
    @app.get('/api/projects/{project_id}/offline')
    def offline_project(project_id: int, context: Ctx):
        project_access(context, project_id, lock=True)
        try:
            snapshot = load_snapshot(context.connection, project_id, 0.5)
            matrix, excluded = matrix_from_snapshot(snapshot)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        project = snapshot['project']
        payload = {
            'sources': browser_sources(),
            'matrix': matrix.to_dict(orient='split'),
            'project_id': project_id,
            'project_title': project['title'],
            'revision': project['revision'],
            'budget': project['budget'],
            'excluded': excluded,
            'criteria': [[c['code'], c['name']] for c in snapshot['criteria']],
            'alternatives': {a['code']: a['name'] for a in snapshot['alternatives']},
            'limitations': snapshot['limitations'],
        }
        return JSONResponse(plain(payload), headers={'Cache-Control': 'no-store'})

    @app.post('/api/projects/{project_id}/offline/sync')
    def sync_project(project_id: int, request: Request, context: Ctx,
                     revision: int = Form(ge=0, le=9223372036854775807),
                     criterion: str = Form(), change: int = Form(ge=-50, le=50),
                     v: str = Form(), csrf: str = Form()):
        project_access(context, project_id, analyst=True, lock=True)
        csrf_check(context, csrf, request)
        check_revision(context.connection, project_id, revision)
        if criterion not in CRITERIA:
            raise HTTPException(422, 'Неизвестный критерий.')
        try:
            parameter = parse_v(v)
            actor = context.user['id']
            with context.connection.transaction():
                if change == 0:
                    result = run_for_user(context.connection, project_id, actor, parameter)
                else:
                    snapshot = load_snapshot(context.connection, project_id, parameter)
                    matrix, _ = matrix_from_snapshot(snapshot)
                    weights = calculate_merec(matrix).set_index('criterion')['weight'].to_dict()
                    weights = perturb(weights, criterion, change / 100)
                    items = snapshot['criteria']
                    reason = f'Офлайн-сценарий MEREC: {criterion}, изменение веса {change:+d}%.'
                    form = {'method': 'manual', 'reason': reason}
                    form.update({f"weight_{item['id']}": str(weights[item['code']]) for item in items})
                    selection = weight_store.selection_from_form(context.connection, project_id, form, items)
                    result = weight_store.run(context.connection, project_id, actor, parameter, selection)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return JSONResponse(plain(result), status_code=201 if result['created'] else 200)

    @app.get('/api/projects/{project_id}/calculations/{result_id}/preview')
    def browser_input(project_id: int, result_id: int, context: Ctx):
        project_access(context, project_id)
        data = load_pair(context.connection, project_id, result_id)
        if data is None:
            raise HTTPException(404, 'Исходный расчёт не найден.')
        matrix, _ = matrix_from_snapshot(data['snapshot'])
        payload = {
            'sources': browser_sources(),
            'matrix': matrix.to_dict(orient='split'),
            'project_title': data['snapshot']['project']['title'],
            'weights': {row['criterion']: row['weight'] for row in data['weighting']['weights']},
            'rankings': {
                method: data['calculated'][method]['ranking']
                for method in ('marcos', 'vikor')
            },
        }
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
