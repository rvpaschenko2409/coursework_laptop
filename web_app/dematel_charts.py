"""Python causal map and thresholded total-influence graph; no browser math."""
from io import BytesIO
from math import cos,sin,pi
from textwrap import fill
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.patches import FancyArrowPatch
from web_app.calculation_charts import LOCK


def chart_bytes(data,kind):
    with LOCK:
        fig=Figure(figsize=(8,5.5),dpi=140,layout='constrained',facecolor='white');ax=fig.subplots()
        rows=data['output']['indicators'];labels={r['code']:f'C{i+1}' for i,r in enumerate(data['snapshot']['criteria'])}
        colors={'cause':'#2457b6','effect':'#b65b20','neutral':'#53657b'}
        if kind=='map':
            grouped={}
            for row in rows:
                point=(row['D_plus_R'],row['D_minus_R'])
                grouped.setdefault(point,{'labels':[],'group':row['group']})['labels'].append(labels[row['code']])
            for (x,y),group in grouped.items():
                ax.scatter(x,y,s=85,c=colors[group['group']],zorder=3)
                ax.annotate(fill(', '.join(group['labels']),22),(x,y),xytext=(8,9),textcoords='offset points',fontsize=12)
            ax.axhline(0,color='#66778d',lw=1);ax.grid(alpha=.18);ax.margins(.2)
            ax.set_xlabel('D + R — общая вовлечённость');ax.set_ylabel('D − R — баланс влияния')
            ax.set_title('DEMATEL: карта влияния критериев')
        else:
            codes=[r['code'] for r in data['snapshot']['criteria']]
            positions={c:(cos(pi/2-2*pi*i/len(codes)),sin(pi/2-2*pi*i/len(codes))) for i,c in enumerate(codes)}
            edges=data['output']['edges'];largest=max([e['value'] for e in edges],default=1)
            for edge in edges:
                arrow=FancyArrowPatch(positions[edge['from']],positions[edge['to']],arrowstyle='-|>',mutation_scale=15,
                    shrinkA=23,shrinkB=23,linewidth=.9+2*edge['value']/largest,color='#8b9db4',connectionstyle='arc3,rad=0.09',zorder=1)
                ax.add_patch(arrow)
            for row in rows:
                x,y=positions[row['code']];ax.scatter(x,y,s=1100,c=colors[row['group']],zorder=3)
                ax.text(x,y,labels[row['code']],ha='center',va='center',color='white',weight='bold',fontsize=12)
            ax.set_xlim(-1.4,1.4);ax.set_ylim(-1.4,1.4);ax.set_aspect('equal');ax.axis('off')
            ax.set_title('Связи полного влияния T выше порога')
            if not edges:ax.text(0,-1.25,'При выбранном пороге связей нет',ha='center',fontsize=11)
        stream=BytesIO();FigureCanvasAgg(fig).print_png(stream)
        return stream.getvalue()
