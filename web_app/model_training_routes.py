"""Role-protected queue, status and explicit activation with immutable model history."""
import secrets
import uuid
from fastapi import HTTPException,Request
from fastapi.responses import HTMLResponse
from starlette.background import BackgroundTask

from web_app import model_training_store as store
from web_app.alternative_store import check_revision
from web_app.laptop_predictions import ROOT,load_pipeline
from web_app.project_store import audit
from web_app.security import csrf_check
from web_app.model_training_worker import TrainingService,install_lifespan


def register_routes(app,Ctx,render,response,project_access):
    service=TrainingService(app.state.database);app.state.training_service=service
    install_lifespan(app,service)

    def notify(result):
        result.background=BackgroundTask(service.kick)
        return result

    async def form(request,context,allowed):
        values=await request.form(max_files=0,max_fields=8)
        if len(values)!=len(values.multi_items()) or set(values)!=allowed or any(not isinstance(v,str) for v in values.values()):raise HTTPException(422,'Некорректные или повторяющиеся поля формы.')
        csrf_check(context,values.get('csrf',''),request)
        return values

    @app.get('/projects/{project_id}/models',response_class=HTMLResponse)
    @app.get('/api/projects/{project_id}/models')
    def page(project_id:int,request:Request,context:Ctx):
        project=project_access(context,project_id);store.ready(context.connection)
        data=dict(project=project,datasets=store.sources(context.connection,project_id),runs=store.listing(context.connection,project_id),
                  models=store.models(context.connection,project_id),request_key=secrets.token_urlsafe(24))
        if request.url.path.startswith('/api/'):
            return notify(response(request,context,data))
        return notify(render(request,context,'model_training.html',**data))

    @app.get('/projects/{project_id}/models/compare', response_class=HTMLResponse)
    def compare(project_id: int, request: Request, context: Ctx,
                first: uuid.UUID | None = None, second: uuid.UUID | None = None):
        project = project_access(context, project_id)
        store.ready(context.connection)
        if (first is None) != (second is None) or (first is not None and first == second):
            raise HTTPException(422, 'Выберите два разных завершённых запуска.')
        result = store.comparison(context.connection, project_id, first, second) if first is not None else None
        return render(request, context, 'model_training_comparison.html', project=project,
                      options=store.completed_options(context.connection, project_id),
                      first=first, second=second, comparison=result)

    @app.post('/projects/{project_id}/models/train')
    @app.post('/api/projects/{project_id}/models/train')
    async def train(project_id:int,request:Request,context:Ctx):
        project_access(context,project_id,analyst=True,lock=True);store.ready(context.connection)
        values=await form(request,context,{'csrf','revision','dataset_id','seed','request_key'})
        previous=context.connection.execute('SELECT id FROM web_training_runs WHERE project_id=%s AND created_by=%s AND request_key=%s',(project_id,context.user['id'],values['request_key'])).fetchone()
        if previous:rid,created=str(previous['id']),False
        else:
            check_revision(context.connection,project_id,values['revision'])
            try:did=int(values['dataset_id'])
            except ValueError:raise HTTPException(422,'Выберите набор данных.') from None
            try:rid,created=store.create(context.connection,project_id,context.user['id'],did,values['seed'],values['request_key'])
            except ValueError as exc:raise HTTPException(422,str(exc)) from None
        return notify(response(request,context,{'run_id':rid,'created':created},status=202 if created else 200,target=f'/projects/{project_id}/models#run-{rid}'))

    @app.post('/projects/{project_id}/models/runs/{run_id}/cancel')
    @app.post('/api/projects/{project_id}/models/runs/{run_id}/cancel')
    async def cancel(project_id:int,run_id:uuid.UUID,request:Request,context:Ctx):
        project_access(context,project_id,analyst=True,lock=True);store.ready(context.connection)
        await form(request,context,{'csrf'})
        row=context.connection.execute('SELECT status FROM web_training_runs WHERE project_id=%s AND id=%s FOR UPDATE',(project_id,run_id)).fetchone()
        if not row:raise HTTPException(404,'Запуск не найден.')
        if row['status'] in ('queued','running'):
            context.connection.execute('UPDATE web_training_runs SET cancel_requested=true WHERE id=%s',(run_id,))
            audit(context.connection,project_id,context.user['id'],'web_model_training_cancel_requested',{'run_id':str(run_id)})
        return notify(response(request,context,{'cancel_requested':True},target=f'/projects/{project_id}/models'))

    @app.post('/projects/{project_id}/models/{model_id}/activate')
    @app.post('/api/projects/{project_id}/models/{model_id}/activate')
    async def activate(project_id:int,model_id:int,request:Request,context:Ctx):
        project_access(context,project_id,analyst=True,lock=True);store.ready(context.connection)
        values=await form(request,context,{'csrf','revision'})
        check_revision(context.connection,project_id,values['revision'])
        model=context.connection.execute('SELECT * FROM ml_models WHERE project_id=%s AND id=%s',(project_id,model_id)).fetchone()
        if not model:raise HTTPException(404,'Модель не найдена в проекте.')
        try:load_pipeline(str(ROOT),model['artifact_path'],model['artifact_sha256'],model['algorithm'])
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
        if not model['is_active']:
            context.connection.execute('UPDATE ml_models SET is_active=false WHERE project_id=%s AND is_active',(project_id,))
            context.connection.execute('UPDATE ml_models SET is_active=true WHERE project_id=%s AND id=%s',(project_id,model_id))
            # A changed active model invalidates open forms and offline synchronization based on the old revision.
            context.connection.execute('UPDATE projects SET updated_at=now() WHERE id=%s',(project_id,))
            audit(context.connection,project_id,context.user['id'],'web_model_activated',{'model_id':model_id,'dataset_id':model['dataset_id']})
        return response(request,context,{'active_model_id':model_id},target=f'/projects/{project_id}/models')
