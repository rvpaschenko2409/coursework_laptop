"""Role-protected project editing, copying, and confirmed deletion."""
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from web_app import project_store as store
from web_app.alternative_store import check_revision
from web_app.security import csrf_check


def register_routes(app, Ctx, render, response, project_access):
    async def posted_form(request: Request):
        form = await request.form()
        if len(form) != len(form.multi_items()) or any(not isinstance(value, str) for value in form.values()):
            raise HTTPException(422, 'Поля формы должны быть текстовыми и не повторяться.')
        return dict(form)

    Posted = Annotated[dict, Depends(posted_form)]

    def page(project_id, request, context, action):
        project = project_access(context, project_id, analyst=True)
        fields = dict(project)
        fields['budget'] = str(project['budget']) if project['budget'] is not None else ''
        if action == 'copy':
            fields['title'] = (project['title'][:191] + ' — копия')[:200]
        return render(request, context, 'project_manage.html', project=project, fields=fields,
                      action=action, request_key=secrets.token_urlsafe(24))

    @app.get('/projects/{project_id}/edit', response_class=HTMLResponse)
    def edit_page(project_id: int, request: Request, context: Ctx):
        return page(project_id, request, context, 'edit')

    @app.get('/projects/{project_id}/copy', response_class=HTMLResponse)
    def copy_page(project_id: int, request: Request, context: Ctx):
        return page(project_id, request, context, 'copy')

    @app.get('/projects/{project_id}/delete', response_class=HTMLResponse)
    def delete_page(project_id: int, request: Request, context: Ctx):
        return page(project_id, request, context, 'delete')

    @app.post('/projects/{project_id}/edit')
    @app.post('/api/projects/{project_id}/edit')
    def edit(project_id: int, request: Request, context: Ctx, form: Posted):
        csrf_check(context, form.get('csrf', ''), request)
        project = project_access(context, project_id, analyst=True, lock=True)
        fields = store.validate_fields(form.get('title', ''), form.get('goal', ''), form.get('budget', ''))
        check_revision(context.connection, project_id, form.get('revision'))
        if any(project[key] != value for key, value in fields.items()):
            context.connection.execute(
                'UPDATE projects SET title=%s,goal=%s,budget=%s WHERE id=%s',
                (fields['title'], fields['goal'], fields['budget'], project_id),
            )
            store.audit(context.connection, project_id, context.user['id'], 'web_project_edit',
                        {'previous_revision': project['revision']})
        return response(request, context, {'project_id': project_id}, target=f'/projects/{project_id}')

    @app.post('/projects/{project_id}/copy')
    @app.post('/api/projects/{project_id}/copy')
    def copy(project_id: int, request: Request, context: Ctx, form: Posted):
        csrf_check(context, form.get('csrf', ''), request)
        project_access(context, project_id, analyst=True, lock=True)
        fields = store.validate_fields(form.get('title', ''), form.get('goal', ''), form.get('budget', ''))
        key = form.get('request_key', '')
        if not 16 <= len(key) <= 128:
            raise HTTPException(422, 'Откройте форму копирования заново.')
        # Replay an existing successful request before checking the source revision.
        previous = context.connection.execute(
            """SELECT 1 FROM audit_events WHERE project_id=%s AND actor_id=%s
               AND action='web_project_copy' AND details->>'request_key'=%s""",
            (project_id, context.user['id'], key),
        ).fetchone()
        if not previous:
            check_revision(context.connection, project_id, form.get('revision'))
        new_id, created = store.copy_inputs(context.connection, project_id, context.user['id'], fields, key)
        return response(request, context, {'project_id': new_id, 'created': created},
                        status=201 if created else 200, target=f'/projects/{new_id}')

    @app.post('/projects/{project_id}/delete')
    @app.post('/api/projects/{project_id}/delete')
    def delete(project_id: int, request: Request, context: Ctx, form: Posted):
        csrf_check(context, form.get('csrf', ''), request)
        project = project_access(context, project_id, analyst=True, lock=True)
        check_revision(context.connection, project_id, form.get('revision'))
        if form.get('confirmation', '') != project['title']:
            raise HTTPException(422, 'Введите точное название проекта для подтверждения удаления.')
        context.connection.execute('DELETE FROM projects WHERE id=%s', (project_id,))
        return response(request, context, {'deleted_project_id': project_id}, target='/projects')
