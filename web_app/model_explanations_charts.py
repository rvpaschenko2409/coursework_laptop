"""Exportable figures drawn only from persisted explanation numbers."""
from io import BytesIO
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg

BG='#111d30';TEXT='#ecf2ff';MUTED='#b0bfd6';MINT='#8de5d1';PEACH='#ffb89e';PURPLE='#b5a2ff'


def png(output,kind):
    fig=Figure(figsize=(10,5.4),dpi=140,facecolor=BG)
    FigureCanvasAgg(fig);ax=fig.add_subplot(111);ax.set_facecolor(BG)
    ax.tick_params(colors=TEXT,labelsize=10)
    for spine in ax.spines.values():spine.set_color('#42546e')
    ax.grid(axis='x',alpha=.16,color=MUTED,zorder=0)
    if kind=='importance':
        result=output['importance'];rows=result['rows'];y=np.arange(len(rows))
        ax.barh(y,[r['mean'] for r in rows],xerr=[r['std'] for r in rows],
            color=[MINT if r['mean']>=0 else PEACH for r in rows],error_kw={'ecolor':MUTED,'capsize':3},zorder=3)
        ax.set_yticks(y,[r['label'] for r in rows]);ax.invert_yaxis();ax.axvline(0,color=MUTED,lw=1)
        ax.set_xlabel('Рост MAE после перемешивания, баллы',color=TEXT,labelpad=12)
        ax.set_title('Перестановочная важность признаков\n'+str(result['repeats'])+' повторов; полосы погрешности — ±1 стандартное отклонение',color=TEXT,fontsize=12,pad=18)
    elif kind=='shap':
        result=output['local'];rows=sorted(result['rows'],key=lambda r:abs(r['contribution']),reverse=True)
        labels=['Базовое значение']+[r['label'] for r in rows]+['Прогноз модели']
        base=result['base_value'];current=base;points=[0,base]
        ax.barh(0,abs(base),left=min(0,base),color='#7b91bc',height=.65,zorder=3)
        annotations=[(0,max(0,base),f'{base:.2f}')]
        for i,row in enumerate(rows,1):
            value=row['contribution'];nxt=current+value
            ax.barh(i,abs(value),left=min(current,nxt),color=MINT if value>=0 else PEACH,height=.65,zorder=3)
            ax.plot([current,current],[i-1+.33,i-.33],color=MUTED,lw=.8)
            annotations.append((i,max(current,nxt),f'{value:+.2f}'));points.append(nxt);current=nxt
        end=len(labels)-1;prediction=result['prediction']
        ax.barh(end,abs(prediction),left=min(0,prediction),color=PURPLE,height=.65,zorder=3)
        annotations.append((end,max(0,prediction),f'{prediction:.2f}'));points.append(prediction)
        low=min(points);high=max(points);span=max(high-low,1)
        for y,x,label in annotations:ax.text(x+span*.012,y,label,color=TEXT,va='center',fontsize=10)
        ax.set_xlim(low-span*.04,high+span*.2)
        ax.set_yticks(np.arange(len(labels)),labels);ax.invert_yaxis()
        ax.set_xlabel('Условный балл производительности',color=TEXT,labelpad=12)
        ax.set_title('SHAP: от базового значения к прогнозу\nЗелёный вклад повышает прогноз, оранжевый — понижает',color=TEXT,fontsize=12,pad=18)
    else:raise ValueError('Unknown chart')
    fig.tight_layout(pad=1.5)
    buffer=BytesIO();fig.savefig(buffer,format='png',facecolor=BG);return buffer.getvalue()
