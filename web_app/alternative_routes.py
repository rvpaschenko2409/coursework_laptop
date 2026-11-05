"""HTML and form API for scoped alternative editing."""
from typing import Annotated
from fastapi import Request,Depends,HTTPException
from fastapi.responses import HTMLResponse
from web_app import alternative_store as store
from web_app.alternative_validation import MANUAL,HARDWARE,InvalidForm,validate_form,in_training_profiles
from web_app.laptop_predictions import active_model
from web_app.criterion_store import manual_fields
from web_app.security import csrf_check


def register_routes(app,Ctx,render,response,project_access,require_user):
    async def posted_form(request: Request):
        form=await request.form()
        if any(not isinstance(v,str) for _,v in form.multi_items()) or len(form)!=len(form.multi_items()):
            raise HTTPException(422,'Поля формы не должны повторяться или содержать файлы.')
        return dict(form)
    Posted=Annotated[dict,Depends(posted_form)]

    def access(context,pid,write=False,expert=False):
        store.ready(context.connection)
        project=project_access(context,pid,lock=write)
        roles=('analyst','expert') if expert else ('analyst',)
        if project['role'] not in roles:raise HTTPException(403,'У вашей роли нет права на это действие.')
        return project

    def editor(context,pid,aid=None,expert=False):
        project=access(context,pid,expert=expert)
        row=store.alternative(context.connection,pid,aid) if aid is not None else None
        scores=store.current_scores(context.connection,pid,aid) if row else {}
        if row:
            fields={**row,**row['features'],**{k:str(v['value']) for k,v in scores.items()},'revision':str(project['revision']),
                'performance_mode':'override' if scores.get('performance_score',{}).get('source')=='manual' else 'auto',
                'performance_override':str(scores['performance_score']['value']) if scores.get('performance_score',{}).get('source')=='manual' else '','change_reason':''}
        else:
            codes={x['code'] for x in context.connection.execute('SELECT code FROM alternatives WHERE project_id=%s',(pid,)).fetchall()}
            code=next((f'A{i}' for i in range(1,100) if f'A{i}' not in codes),'')
            fields={'code':code,'name':'','description':'','cpu_cores':'4','cpu_threads':'8','cpu_frequency_ghz':'2.5','ram_gb':'8','storage_gb':'512','storage_type':'SSD','performance_mode':'auto','revision':str(project['revision'])}
        model=active_model(context.connection,pid)
        history=[]
        if row:
            history=[dict(h) for h in context.connection.execute('''SELECT h.*,c.name AS criterion_name,u.name AS actor_name FROM score_history h
                JOIN scores s ON s.project_id=h.project_id AND s.id=h.score_id JOIN criteria c ON c.project_id=s.project_id AND c.id=s.criterion_id
                JOIN users u ON u.id=h.actor_id WHERE h.project_id=%s AND s.alternative_id=%s ORDER BY h.id DESC LIMIT 30''',(pid,aid)).fetchall()]
        return {'project':project,'alternative':row,'fields':fields,'errors':{},'manual_fields':manual_fields(context.connection,pid),'hardware_fields':HARDWARE,
            'expert':expert,'active_model':model,'current_performance':scores.get('performance_score'),
            'history':history,'outside_profiles':bool(row and not in_training_profiles(row['features']))}

    @app.get('/projects/{project_id}/alternatives/new',response_class=HTMLResponse)
    def create_page(project_id:int,request:Request,context:Ctx):
        return render(request,context,'alternative_form.html',**editor(context,project_id))

    @app.get('/projects/{project_id}/alternatives/{alternative_id}/edit',response_class=HTMLResponse)
    def edit_page(project_id:int,alternative_id:int,request:Request,context:Ctx):
        return render(request,context,'alternative_form.html',**editor(context,project_id,alternative_id))

    @app.get('/projects/{project_id}/alternatives/{alternative_id}/scores',response_class=HTMLResponse)
    def scores_page(project_id:int,alternative_id:int,request:Request,context:Ctx):
        return render(request,context,'alternative_form.html',**editor(context,project_id,alternative_id,expert=True))

    @app.get('/api/projects/{project_id}/alternatives/{alternative_id}')
    def alternative_api(project_id:int,alternative_id:int,context:Ctx):
        require_user(context);project_access(context,project_id)
        row=store.alternative(context.connection,project_id,alternative_id)
        return {'alternative':row,'scores':list(store.current_scores(context.connection,project_id,alternative_id).values())}

    def submit(request,context,pid,aid,form,expert=False):
        require_user(context);csrf_check(context,form.get('csrf',''),request)
        access(context,pid,write=True,expert=expert)
        store.check_revision(context.connection,pid,form.get('revision'))
        existing=store.alternative(context.connection,pid,aid) if aid is not None else None
        manual=manual_fields(context.connection,pid)
        allowed={'csrf','revision','change_reason',*manual}
        if not expert:allowed.update({'code','name','description',*HARDWARE,'storage_type','performance_mode','performance_override'})
        if set(form)-allowed:raise HTTPException(422,'В форме есть неизвестные или недоступные для этой роли поля.')
        try:
            values=validate_form(form,expert,manual)
            with context.connection.transaction():result=store.save(context.connection,pid,context.user['id'],values,existing,expert)
        except InvalidForm as exc:
            if request.url.path.startswith('/api/'):return response(request,context,{'detail':str(exc),'errors':exc.errors},422)
            data=editor(context,pid,aid,expert);data.update(fields=form,errors=exc.errors)
            return render(request,context,'alternative_form.html',422,**data)
        return response(request,context,result,201 if aid is None else 200,target=f'/projects/{pid}')

    @app.post('/projects/{project_id}/alternatives')
    @app.post('/api/projects/{project_id}/alternatives')
    def create_alternative(project_id:int,request:Request,context:Ctx,form:Posted):
        return submit(request,context,project_id,None,form)

    @app.post('/projects/{project_id}/alternatives/{alternative_id}')
    @app.post('/api/projects/{project_id}/alternatives/{alternative_id}')
    def edit_alternative(project_id:int,alternative_id:int,request:Request,context:Ctx,form:Posted):
        return submit(request,context,project_id,alternative_id,form)

    @app.post('/projects/{project_id}/alternatives/{alternative_id}/scores')
    @app.post('/api/projects/{project_id}/alternatives/{alternative_id}/scores')
    def edit_scores(project_id:int,alternative_id:int,request:Request,context:Ctx,form:Posted):
        return submit(request,context,project_id,alternative_id,form,True)

    @app.get('/projects/{project_id}/alternatives/{alternative_id}/delete',response_class=HTMLResponse)
    def delete_page(project_id:int,alternative_id:int,request:Request,context:Ctx):
        project=access(context,project_id)
        return render(request,context,'alternative_delete.html',project=project,alternative=store.alternative(context.connection,project_id,alternative_id),errors={},fields={})

    @app.post('/projects/{project_id}/alternatives/{alternative_id}/delete')
    @app.post('/api/projects/{project_id}/alternatives/{alternative_id}/delete')
    def delete_alternative(project_id:int,alternative_id:int,request:Request,context:Ctx,form:Posted):
        require_user(context);csrf_check(context,form.get('csrf',''),request)
        project=access(context,project_id,write=True);store.check_revision(context.connection,project_id,form.get('revision'))
        row=store.alternative(context.connection,project_id,alternative_id)
        try:
            with context.connection.transaction():result=store.delete(context.connection,project_id,context.user['id'],row,form.get('change_reason',''),form.get('confirm_code',''))
        except InvalidForm as exc:
            if request.url.path.startswith('/api/'):return response(request,context,{'detail':str(exc),'errors':exc.errors},422)
            return render(request,context,'alternative_delete.html',422,project=project,alternative=row,fields=form,errors=exc.errors)
        return response(request,context,result,target=f'/projects/{project_id}')

    @app.get('/projects/{project_id}/deleted-alternatives',response_class=HTMLResponse)
    def deleted_alternatives(project_id:int,request:Request,context:Ctx):
        project=access(context,project_id)
        rows=[dict(r) for r in context.connection.execute('''SELECT a.id,a.code,a.name,a.reason,a.deleted_at,u.name AS actor_name FROM web_deleted_alternatives a
            JOIN users u ON u.id=a.deleted_by WHERE a.project_id=%s ORDER BY a.id DESC LIMIT 100''',(project_id,)).fetchall()]
        return render(request,context,'alternative_archive.html',project=project,rows=rows)

    @app.get('/api/projects/{project_id}/deleted-alternatives/{archive_id}')
    def archived_alternative(project_id:int,archive_id:int,context:Ctx):
        access(context,project_id)
        row=context.connection.execute('SELECT * FROM web_deleted_alternatives WHERE project_id=%s AND id=%s',(project_id,archive_id)).fetchone()
        if not row:raise HTTPException(404,'Запись не найдена.')
        return dict(row)
