"""Authenticated, snapshot-based explanation pages and scientific PNG exports."""
from fastapi import HTTPException,Request
from fastapi.responses import HTMLResponse,Response,JSONResponse,RedirectResponse
from fastapi.encoders import jsonable_encoder

from web_app import model_explanations_store as store
from web_app.security import csrf_check


def register_routes(app,Ctx,render,response,project_access):
    @app.get('/projects/{project_id}/explanations',response_class=HTMLResponse)
    def page(project_id:int,request:Request,context:Ctx,prediction_id:int|None=None):
        project=project_access(context,project_id);store.ready(context.connection)
        items=store.predictions(context.connection,project_id)
        if prediction_id is not None and not any(p['id']==prediction_id for p in items):raise HTTPException(404,'Прогноз не найден среди доступных прогнозов проекта.')
        return render(request,context,'model_explanations.html',project=project,predictions=items,
                      selected_prediction=prediction_id,history=store.history(context.connection,project_id),record=None,current=None)

    @app.get('/projects/{project_id}/alternatives/{alternative_id}/explanation')
    def alternative(project_id:int,alternative_id:int,context:Ctx):
        project_access(context,project_id);store.ready(context.connection)
        pred=context.connection.execute('''SELECT s.prediction_id FROM scores s JOIN criteria c ON c.project_id=s.project_id AND c.id=s.criterion_id
            WHERE s.project_id=%s AND s.alternative_id=%s AND c.code='performance_score' AND s.prediction_id IS NOT NULL''',(project_id,alternative_id)).fetchone()
        if not pred:raise HTTPException(404,'У этой оценки пока нет сохранённого ML-прогноза для объяснения.')
        return RedirectResponse(f'/projects/{project_id}/explanations?prediction_id={pred["prediction_id"]}',status_code=303)

    @app.post('/projects/{project_id}/explanations')
    @app.post('/api/projects/{project_id}/explanations')
    async def calculate(project_id:int,request:Request,context:Ctx):
        project_access(context,project_id);store.ready(context.connection)
        form=await request.form(max_files=0,max_fields=2)
        if set(form)!={'csrf','prediction_id'} or len(form)!=len(form.multi_items()) or any(not isinstance(v,str) for v in form.values()):raise HTTPException(422,'Выберите сохранённый прогноз без повторяющихся полей.')
        csrf_check(context,form['csrf'],request)
        try:pid=int(form['prediction_id'])
        except ValueError:raise HTTPException(422,'Выберите сохранённый прогноз.') from None
        from starlette.concurrency import run_in_threadpool
        eid,created=await run_in_threadpool(store.calculate,context.connection,project_id,context.user['id'],pid)
        return response(request,context,{'explanation_id':eid,'created':created},status=201 if created else 200,
                        target=f'/projects/{project_id}/explanations/{eid}')

    @app.get('/projects/{project_id}/explanations/{explanation_id}',response_class=HTMLResponse)
    @app.get('/api/projects/{project_id}/explanations/{explanation_id}')
    def result(project_id:int,explanation_id:int,request:Request,context:Ctx):
        project=project_access(context,project_id);store.ready(context.connection)
        record=store.result(context.connection,project_id,explanation_id)
        if request.url.path.startswith('/api/'):return response(request,context,record)
        aid=record['input_snapshot']['prediction']['alternative_id']
        current=context.connection.execute('''SELECT s.value,s.source::text AS source,s.prediction_id,s.override_reason,a.code FROM scores s
            JOIN criteria c ON c.project_id=s.project_id AND c.id=s.criterion_id
            JOIN alternatives a ON a.project_id=s.project_id AND a.id=s.alternative_id
            WHERE s.project_id=%s AND s.alternative_id=%s AND c.code='performance_score' ''',(project_id,aid)).fetchone()
        return render(request,context,'model_explanations.html',project=project,predictions=[],selected_prediction=None,
                      history=store.history(context.connection,project_id),record=record,current=current)

    @app.get('/projects/{project_id}/explanations/{explanation_id}/snapshot.json')
    def snapshot(project_id:int,explanation_id:int,context:Ctx):
        project_access(context,project_id);store.ready(context.connection)
        record=store.result(context.connection,project_id,explanation_id)
        return JSONResponse(jsonable_encoder(record),headers={'Content-Disposition':f'attachment; filename="explanation_{explanation_id}.json"'})

    @app.get('/projects/{project_id}/explanations/{explanation_id}/{kind}.png')
    def chart(project_id:int,explanation_id:int,kind:str,context:Ctx):
        project_access(context,project_id);store.ready(context.connection)
        if kind not in ('importance','shap'):raise HTTPException(404)
        from web_app.model_explanations_charts import png
        record=store.result(context.connection,project_id,explanation_id)
        return Response(png(record['output'],kind),media_type='image/png',
                        headers={'Content-Disposition':f'inline; filename="explanation_{explanation_id}_{kind}.png"'})
