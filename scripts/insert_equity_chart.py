import csv
from pathlib import Path
p=Path('outputs/pandadata_exact_20260918_adaptive_only/weekly')
eq=list(csv.DictReader((p/'adaptive/equity.csv').open(encoding='utf-8-sig'))); pts=eq[::max(1,len(eq)//180)]
if pts[-1]['date']!=eq[-1]['date']: pts.append(eq[-1])
vs=[float(x['nav'])/float(eq[0]['nav']) for x in pts]; bs=[float(x['benchmark']) for x in pts]; lo=min(vs+bs); hi=max(vs+bs); W,H,pad=900,280,34
def xy(i,v): return f'{pad+i*(W-2*pad)/max(1,len(pts)-1):.1f},{H-pad-(v-lo)/max(1e-9,hi-lo)*(H-2*pad):.1f}'
svg=f'<svg viewBox="0 0 900 280" role="img" aria-label="累计收益曲线"><rect width="100%" height="100%" fill="#fff"/><line x1="34" y1="246" x2="866" y2="246" stroke="#e5e7eb"/><polyline points="{" ".join(xy(i,v) for i,v in enumerate(bs))}" fill="none" stroke="#94a3b8" stroke-width="2"/><polyline points="{" ".join(xy(i,v) for i,v in enumerate(vs))}" fill="none" stroke="#0f766e" stroke-width="3"/><text x="42" y="24" fill="#0f766e" font-size="13">策略</text><text x="92" y="24" fill="#64748b" font-size="13">沪深300</text></svg>'
f=p/'report.html'; h=f.read_text(encoding='utf-8'); h=h.replace('<div class="chart"></div>','<div class="chart">'+svg+'</div>'); f.write_text(h,encoding='utf-8'); print('chart inserted',len(h))