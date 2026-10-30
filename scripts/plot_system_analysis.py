from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch,FancyArrowPatch
B=Path(__file__).resolve().parents[1]/'results'/'figures'
B.mkdir(parents=True, exist_ok=True)
fig,ax=plt.subplots(figsize=(13,8));ax.set(xlim=(0,13),ylim=(0,8));ax.axis('off')
plt.rcParams['font.family']='DejaVu Sans'
def box(x,y,w,h,text,fill='#f3f3f3'):
 ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle='round,pad=0.05',facecolor=fill,edgecolor='#555',lw=1))
 ax.text(x+w/2,y+h/2,text,ha='center',va='center',fontsize=10,linespacing=1.5)
def arrow(a,b,label=None,offset=(0,0),rad=0):
 ax.add_patch(FancyArrowPatch(a,b,arrowstyle='-|>',mutation_scale=13,connectionstyle=f'arc3,rad={rad}',color='#555',lw=1.2))
 if label:ax.text((a[0]+b[0])/2+offset[0],(a[1]+b[1])/2+offset[1],label,ha='center',va='center',fontsize=8,bbox={'facecolor':'white','edgecolor':'none','pad':2})
box(.3,6.2,3.1,1.1,'Офисные сотрудники\nЗадачи, удобство, автономность')
box(4.7,6.2,3.4,1.1,'Руководитель — ЛПР\nБюджет и утверждение выбора')
box(9.4,6.2,3.2,1.1,'ИТ-специалист\nСовместимость и характеристики')
box(.3,3.25,3.1,1.25,'Специалист по закупкам\nВарианты и условия закупки\nУчебные альтернативы A1–A5')
box(4.7,3.1,3.4,1.6,'СППР\nУсловный прогноз ML\nMEREC → MARCOS и VIKOR\nПроверки и объяснения','#e8edf1')
box(9.4,3.25,3.2,1.25,'Рекомендация для ЛПР\nРейтинги и компромисс\nРасхождения методов')
box(.5,.45,5.3,1.25,'Противоречия\nЦена ↔ производительность\nМощность ↔ масса и время без зарядки','#fff5e8')
box(7.1,.45,5.3,1.25,'Ограничения\nБюджет, полнота и сопоставимость данных\nОшибка ML; синтетическая шкала','#fff5e8')
arrow((1.85,6.2),(1.85,4.5),'требования',(.6,0))
arrow((6.4,6.2),(6.4,4.7),'ограничения',(.7,0))
arrow((11,6.2),(8.1,4.3),'проверка сведений',(.1,.15))
arrow((3.4,3.9),(4.7,3.9),'данные',(0,.3))
arrow((8.1,3.9),(9.4,3.9),'расчёт',(0,.3))
arrow((11,4.5),(8.1,6.55),'для утверждения',(.5,.4),rad=-.3)
arrow((9.8,3.25),(3.1,3.25),'уточнение требований и повторное сравнение',(0,-.55),rad=-.18)
arrow((3.1,1.7),(4.7,3.2))
arrow((9.7,1.7),(8.1,3.2))
ax.text(6.5,7.8,'Богатая картина учебного сценария выбора ноутбуков',ha='center',fontsize=15,weight='bold')
fig.tight_layout();fig.savefig(B/'04_rich_picture.png',dpi=160,facecolor='white');plt.close(fig)
