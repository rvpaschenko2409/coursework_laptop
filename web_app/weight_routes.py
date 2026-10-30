"""Explicit per-run weight choice; no hidden changes to previous calculations."""
from typing import Annotated,Literal
from fastapi import Request,Depends,HTTPException,Query
from fastapi.responses import HTMLResponse
from web_app.security import csrf_check
from web_app.alternative_store import ready,check_revision
from web_app.calculation_store import parse_v,run_for_user
from web_app.dematel_store import criteria
from web_app import weight_store as store
from web_app.weight_math import LABELS


def register_routes(app,Ctx,render,response,project_access,require_user):
    async def posted(request:Request):
        form=await request.form()
        if len(form)!=len(form.multi_items()) or any(not isinstance(v,str) for _,v in form.multi_items()):raise HTTPException(422,'Поля формы не должны повторяться или содержать файлы.')
        return dict(form)
    Posted=Annotated[dict,Depends(posted)]

    def page(context,pid,values):
        project=project_access(context,pid);items=criteria(context.connection,pid)
        sources=context.connection.execute('''SELECT s.id,s.project_revision,s.created_at,u.name AS actor_name
            FROM structure_models s JOIN users u ON u.id=s.created_by
            WHERE s.project_id=%s AND s.method='dematel' AND s.project_revision=%s
            ORDER BY s.id DESC LIMIT 100''',(pid,project['revision'])).fetchall()
        form={'method':'merec','subjective_source':'manual','alpha':'0.5','v':'0.5','reason':'',**values}
        # There is deliberately no pre-filled subjective judgement.
        return {'project':project,'items':items,'sources':sources,'values':form,'labels':LABELS,'error':None}

    @app.get('/projects/{project_id}/weights',response_class=HTMLResponse)
    def weights_page(project_id:int,request:Request,context:Ctx,
        method:Literal['merec','manual','dematel','combined']='merec',
        subjective_source:Literal['manual','dematel']='manual',dematel_id:int|None=Query(None,gt=0)):
        return render(request,context,'weights.html',**page(context,project_id,{'method':method,'subjective_source':subjective_source,'dematel_id':str(dematel_id) if dematel_id else ''}))

    @app.post('/projects/{project_id}/weights')
    @app.post('/api/projects/{project_id}/weights')
    def weights_run(project_id:int,request:Request,context:Ctx,form:Posted):
        require_user(context);csrf_check(context,form.get('csrf',''),request);ready(context.connection)
        project_access(context,project_id,analyst=True,lock=True)
        check_revision(context.connection,project_id,form.get('revision'))
        try:
            v=parse_v(form.get('v','0.5'))
            with context.connection.transaction():
                selection=store.selection_from_form(context.connection,project_id,form,criteria(context.connection,project_id))
                result=run_for_user(context.connection,project_id,context.user['id'],v) if selection is None else store.run(context.connection,project_id,context.user['id'],v,selection)
        except ValueError as exc:
            if request.url.path.startswith('/api/'):return response(request,context,{'detail':str(exc)},422)
            data=page(context,project_id,form);data['error']=str(exc)
            return render(request,context,'weights.html',422,**data)
        return response(request,context,result,201 if result['created'] else 200,target=f"/projects/{project_id}/calculations/{result['result_ids']['marcos']}")
