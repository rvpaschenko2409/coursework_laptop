"""Private raster figures; all numeric values are also available as tables."""
from io import BytesIO
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import TwoSlopeNorm
from matplotlib.ticker import MaxNLocator
from web_app.calculation_charts import LOCK


def chart_bytes(data,kind):
    with LOCK:
        output=data['output'];codes=output['scenarios'][0]['marcos']['comparison']['rank_deltas'].keys()
        if kind=='weights':
            fig=Figure(figsize=(12,7),dpi=130,layout='constrained',facecolor='white');axes=fig.subplots(2,1,sharex=True)
            rows=[r for r in output['scenarios'] if r['kind']=='weight'];items=data['snapshot']['source']['input']['criteria']
            indexes={c['id']:i+1 for i,c in enumerate(items)}
            labels=[f"C{indexes[r['criterion_id']]} {r['delta']*100:+g}%" for r in rows]
            changes=[np.array([[r[m]['comparison']['rank_deltas'][c] for r in rows] for c in codes]) for m in ('marcos','vikor')]
            limit=max(1,max(float(abs(a).max()) for a in changes))
            for ax,values,method in zip(axes,changes,['MARCOS','VIKOR']):
                im=ax.imshow(values,aspect='auto',cmap='RdBu_r',norm=TwoSlopeNorm(0,-limit,limit))
                ax.set_yticks(range(len(codes)),list(codes));ax.set_title(method,loc='left')
                for i in range(len(codes)):
                    for j in range(len(rows)):ax.text(j,i,f'{int(values[i,j]):+d}' if values[i,j] else '0',ha='center',va='center',fontsize=9,color='white' if abs(values[i,j])>limit*.65 else '#14253c')
            axes[-1].set_xticks(range(len(rows)),labels,rotation=55,ha='right')
            bar=fig.colorbar(im,ax=list(axes),shrink=.8,pad=.02);bar.ax.yaxis.set_major_locator(MaxNLocator(integer=True));bar.set_label('Изменение ранга: + хуже, − лучше')
            fig.suptitle('Изменение рангов при варьировании одного веса')
        else:
            fig=Figure(figsize=(9.6,6.2),dpi=130,layout='constrained',facecolor='white');ax=fig.subplots()
            rows=[r for r in output['scenarios'] if r['kind']=='v'];palette=__import__('matplotlib').colormaps['tab20']
            for i,code in enumerate(codes):
                ranks=[next(x['rank'] for x in r['vikor']['ranking'] if x['alternative_code']==code) for r in rows]
                ax.plot([r['v'] for r in rows],ranks,marker='o',markersize=4,label=code,color=palette(i),linestyle=['-','--',':'][i%3])
            ax.axvline(data['snapshot']['source']['input']['parameters']['v'],color='#6b7280',alpha=.7,linestyle='--',label='Исходное v')
            ax.set_xticks([i/10 for i in range(11)]);ax.set_yticks(range(1,len(codes)+1));ax.invert_yaxis()
            ax.set_xlabel('Параметр v');ax.set_ylabel('Ранг VIKOR: 1 — первое место');ax.set_title('VIKOR при фиксированных исходных весах')
            ax.grid(alpha=.2);ax.legend(bbox_to_anchor=(1.02,1),loc='upper left',fontsize=9)
        stream=BytesIO();FigureCanvasAgg(fig).print_png(stream);return stream.getvalue()
