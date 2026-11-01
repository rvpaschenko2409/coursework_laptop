"""Authorized read-only XLSX downloads scoped to a calculation or saved LPR choice."""
from fastapi import HTTPException
from fastapi.responses import Response
from web_app import export_data
from web_app.export_xlsx import export_bytes

MIME='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def register_routes(app,Ctx,project_access):
    def download(context,pid,identifier,kind):
        project_access(context,pid)
        try:
            data=export_data.from_choice(context.connection,pid,identifier) if kind=='choice' else export_data.from_calculation(context.connection,pid,identifier)
            content=export_bytes(data)
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        canonical=next(r['id'] for r in data['source']['records'] if r['method']=='marcos')
        name=f'laptop_project_{pid}_choice_{identifier}.xlsx' if kind=='choice' else f'laptop_project_{pid}_calculation_{canonical}.xlsx'
        return Response(content,media_type=MIME,headers={'Content-Disposition':f'attachment; filename="{name}"',
            'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @app.get('/projects/{project_id}/calculations/{result_id}/export.xlsx')
    @app.get('/api/projects/{project_id}/calculations/{result_id}/export.xlsx')
    def calculation_export(project_id:int,result_id:int,context:Ctx):return download(context,project_id,result_id,'calculation')

    @app.get('/projects/{project_id}/choices/{choice_id}/export.xlsx')
    @app.get('/api/projects/{project_id}/choices/{choice_id}/export.xlsx')
    def choice_export(project_id:int,choice_id:int,context:Ctx):return download(context,project_id,choice_id,'choice')
