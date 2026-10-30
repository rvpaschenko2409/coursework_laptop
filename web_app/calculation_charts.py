"""Exact, server-rendered charts from the verified historical calculation."""
from io import BytesIO
from threading import RLock
from textwrap import shorten
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg

LOCK=RLock()


def chart_bytes(data,kind):
    with LOCK:
        fig=Figure(figsize=(9.6,5.1),dpi=130,layout='constrained',facecolor='white')
        ax=fig.subplots();calculated=data['calculated']
        if kind=='weights':
            weights=data['weighting']['weights']
            labels=[shorten(data['criteria'][r['criterion']]['name'],width=30,placeholder='…') for r in weights]
            values=[r['weight'] for r in weights]
            ax.barh(labels,values,color='#2457b6');ax.invert_yaxis()
            ax.set_xlabel('Вес критерия');ax.set_title(data['weight_label'])
            ax.set_xlim(0,max(values)*1.28 if max(values)>0 else 1)
            for i,value in enumerate(values):ax.text(value+.003,i,f'{value:.4f}',va='center',fontsize=10)
        else:
            rows=data['comparison'];y=list(range(len(rows)))
            ax.barh([i-.18 for i in y],[r['marcos']['rank'] for r in rows],height=.34,label='MARCOS',color='#2457b6')
            ax.barh([i+.18 for i in y],[r['vikor']['rank'] for r in rows],height=.34,label='VIKOR',color='#278779')
            ax.set_yticks(y,[r['code'] for r in rows]);ax.invert_yaxis();ax.set_xticks(list(range(1,len(rows)+1)))
            ax.set_xlim(0,len(rows)+.5);ax.set_xlabel('Ранг: 1 — первое место');ax.set_title('Сравнение рангов');ax.legend(loc='lower right')
        ax.spines[['top','right']].set_visible(False);ax.grid(axis='x',alpha=.18);ax.set_axisbelow(True)
        stream=BytesIO();FigureCanvasAgg(fig).print_png(stream)
        return stream.getvalue()
