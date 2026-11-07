"""Analyst criterion management with CSRF, project locks and stale-form protection."""
from typing import Annotated
from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from web_app import criterion_store as store
from web_app.alternative_store import check_revision
from web_app.security import csrf_check


def register_routes(app, Ctx, render, response, project_access):
    async def posted(request: Request):
        form = await request.form()
        if len(form) != len(form.multi_items()) or any(not isinstance(v, str) for v in form.values()):
            raise HTTPException(422, 'Поля формы не должны повторяться или содержать файлы.')
        return dict(form)
    Posted = Annotated[dict, Depends(posted)]

    def get_row(context, pid, cid, archived=False):
        row = next((r for r in store.items(context.connection, pid, archived) if r['id'] == cid), None)
        if row is None:
            raise HTTPException(404, 'Критерий не найден в этом проекте.')
        return row

    @app.get('/projects/{project_id}/criteria', response_class=HTMLResponse)
    def index(project_id: int, request: Request, context: Ctx):
        project = project_access(context, project_id)
        return render(request, context, 'criteria.html', project=project,
                      items=store.items(context.connection, project_id), archived=store.items(context.connection, project_id, True))

    def editor(pid, cid, action, request, context, calculated=False):
        project = project_access(context, pid, analyst=True)
        row = get_row(context, pid, cid, action == 'restore') if cid else None
        alternatives = [dict(r) for r in context.connection.execute('SELECT id,code,name FROM alternatives WHERE project_id=%s ORDER BY sort_order,id', (pid,)).fetchall()]
        scores = {r['alternative_id']: str(r['value']) for r in context.connection.execute('SELECT alternative_id,value FROM scores WHERE project_id=%s AND criterion_id=%s', (pid, cid)).fetchall()} if cid else {}
        return render(request, context, 'criterion_form.html', project=project, criterion=row,
                      alternatives=alternatives, scores=scores, action=action,
                      calculated=calculated or bool(row and row['source'] == 'calc'),
                      cpu_clock_code=store.CPU_CLOCK, cpu_clock_unit=store.CPU_CLOCK_UNIT)

    @app.get('/projects/{project_id}/criteria/new', response_class=HTMLResponse)
    def new(project_id: int, request: Request, context: Ctx, calculated: bool = False):
        return editor(project_id, None, 'new', request, context, calculated)

    @app.get('/projects/{project_id}/criteria/{criterion_id}/{action}', response_class=HTMLResponse)
    def edit(project_id: int, criterion_id: int, action: str, request: Request, context: Ctx):
        if action not in ('edit', 'delete', 'restore'):
            raise HTTPException(404)
        return editor(project_id, criterion_id, action, request, context)

    def submit(pid, cid, action, form, request, context):
        project_access(context, pid, analyst=True, lock=True)
        csrf_check(context, form.get('csrf', ''), request)
        check_revision(context.connection, pid, form.get('revision'))
        row = get_row(context, pid, cid, action == 'restore') if cid else None
        allowed = {'csrf', 'revision', 'reason'}
        if action in ('delete', 'restore'):
            allowed.add('confirmation')
        else:
            allowed.update({'code', 'name', 'unit', 'direction'})
            if action == 'new':
                allowed.add('source')
            allowed.update(f"score_{a['id']}" for a in context.connection.execute('SELECT id FROM alternatives WHERE project_id=%s', (pid,)).fetchall())
        if set(form) - allowed:
            raise HTTPException(422, 'Неизвестные поля формы.')
        with context.connection.transaction():
            if action in ('delete', 'restore'):
                store.remove_or_restore(context.connection, pid, context.user['id'], row, form, action == 'restore')
            else:
                cid = store.save(context.connection, pid, context.user['id'], form, row)
        return response(request, context, {'criterion_id': cid, 'action': action},
                        201 if action == 'new' else 200, target=f'/projects/{pid}/criteria')

    @app.post('/projects/{project_id}/criteria')
    @app.post('/api/projects/{project_id}/criteria')
    def create(project_id: int, request: Request, context: Ctx, form: Posted):
        return submit(project_id, None, 'new', form, request, context)

    @app.post('/projects/{project_id}/criteria/{criterion_id}/{action}')
    @app.post('/api/projects/{project_id}/criteria/{criterion_id}/{action}')
    def modify(project_id: int, criterion_id: int, action: str, request: Request, context: Ctx, form: Posted):
        if action not in ('edit', 'delete', 'restore'):
            raise HTTPException(404)
        return submit(project_id, criterion_id, action, form, request, context)
