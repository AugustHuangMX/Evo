/* Apex combat analysis: independent signals, explicit denominators, no composite score. */
(() => {
 'use strict';
 const root=document.querySelector('[data-analysis]'); if(!root)return;
 const {rows,metrics}=JSON.parse(root.querySelector('[data-analysis-json]').textContent);
 const $=s=>root.querySelector(s), NS='http://www.w3.org/2000/svg';
 const series=[['kills','击杀','kills'],['assists','助攻','assists'],['knockdowns','击倒','knocks']];
 const fmt=(v,d=1)=>v==null?'—':v.toLocaleString('zh-CN',{maximumFractionDigits:d});
 const values=(items,key)=>items.map(r=>r[key]).filter(v=>v!=null);
 const avg=vs=>vs.length?vs.reduce((a,b)=>a+b,0)/vs.length:null;
 const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
 const svg=(parent,tag,attrs,text)=>{const n=document.createElementNS(NS,tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));if(text!=null)n.textContent=text;parent.append(n);return n;};
 const link=r=>{const a=node('a','查看这局 →');a.href=`/matches?page=${encodeURIComponent(r.page||1)}&open=${encodeURIComponent(r.id)}#m${encodeURIComponent(r.id)}`;return a;};
 const context=r=>`${r.when||'时间未识别'} · ${r.legend} · ${r.map} · ${r.mode} · ${r.rank_tier}${r.review?' · 待确认':''}`;
 const numbers=r=>`击杀 ${fmt(r.kills)} / 助攻 ${fmt(r.assists)} / 击倒 ${fmt(r.knockdowns)} / 伤害 ${fmt(r.damage,0)} / 名次 ${fmt(r.placement,0)} / RP ${fmt(r.rp_delta)}`;
 function details(target,r){target.replaceChildren(node('span',context(r)),node('strong',numbers(r)),link(r));}
 function empty(target,w,text){target.replaceChildren();svg(target,'text',{x:w/2,y:120,'text-anchor':'middle'},text);}
 function extent(vs){let lo=vs.reduce((a,b)=>Math.min(a,b),0),hi=vs.reduce((a,b)=>Math.max(a,b),0);if(hi===lo)hi=lo+1;return[lo,hi];}
 function axes(target,w,h,xs,ys,xname,yname,ends){
  target.replaceChildren();target.setAttribute('viewBox',`0 0 ${w} ${h}`);
  const [xl,xh]=extent(xs),[yl,yh]=extent(ys),x=v=>54+(v-xl)/(xh-xl)*(w-80),y=v=>30+(yh-v)/(yh-yl)*(h-90);
  for(let i=0;i<=4;i++){
   const v=yl+(yh-yl)*i/4;
   svg(target,'line',{x1:54,x2:w-26,y1:y(v),y2:y(v),class:'analysis-gridline'});
   svg(target,'text',{x:46,y:y(v)+4,'text-anchor':'end'},fmt(v,Math.abs(v)<10?1:0));
  }
  if(yl<0)svg(target,'line',{x1:54,x2:w-26,y1:y(0),y2:y(0),class:'analysis-zero'});
  if(xl<0)svg(target,'line',{x1:x(0),x2:x(0),y1:30,y2:h-60,class:'analysis-zero'});
  svg(target,'text',{x:54,y:14},yname);
  svg(target,'text',{x:54,y:h-30},ends?ends[0]:fmt(xl));
  svg(target,'text',{x:w-26,y:h-30,'text-anchor':'end'},ends?ends[1]:fmt(xh));
  svg(target,'text',{x:w/2,y:h-9,'text-anchor':'middle'},xname);
  return{x,y};
 }
 function kpis(){
  const target=$('[data-combat-kpis]');
  series.forEach(([key,label,css])=>{
   const vs=values(rows,key),card=node('div',null,`combat-kpi combat-${css}`);
   card.append(node('span',`场均${label}`),node('strong',fmt(avg(vs))),node('small',`合计 ${fmt(vs.reduce((a,b)=>a+b,0),0)} · 有效 ${vs.length} / ${rows.length} 局`));target.append(card);
  });
  const targetContext=$('[data-combat-context]');
  [['ka','场均 K+A'],['damage_per_min','每存活分钟伤害'],['ka_per10','每存活10分钟 K+A'],['knocks_per10','每存活10分钟击倒'],['revives','场均救起队友'],['respawns','场均重生队友']].forEach(([key,label])=>{
   const vs=values(rows,key),cell=node('div');cell.append(node('span',label),node('strong',fmt(avg(vs))),node('small',`有效 ${vs.length} / ${rows.length} 局`));targetContext.append(cell);
  });
  [['damage_share','伤害占比','本人','队友合计'],['kill_share','击杀占比','本人','队友合计'],['kill_participation','击杀参与率','本人计入 K+A','未计入本人 K+A']].forEach(([key,label,own,other])=>{
   const vs=values(rows,key),mean=avg(vs),cell=node('div',null,'combat-team-share'),head=node('div'),track=node('div',null,'combat-share-track'),bar=node('span');
   head.append(node('span',label),node('strong',mean==null?'—':`${fmt(mean)}%`));bar.style.width=`${mean||0}%`;track.append(bar);
   cell.append(head,track,node('small',`${own} / ${other} · 有效 ${vs.length} / ${rows.length} 局`));$('[data-team-shares]').append(cell);
  });
 }
 let combatPoints=[],combatAt=null,combatScale=null;
 const shown=new Set(series.map(s=>s[0]));
 function selectCombat(index){
  if(!combatPoints.length||!combatScale)return;
  combatAt=Math.max(0,Math.min(combatPoints.length-1,index));const p=combatPoints[combatAt],chart=$('[data-combat-chart]');
  chart.querySelectorAll('[data-combat-cursor]').forEach(n=>n.remove());
  const x=combatScale.x(combatAt);
  svg(chart,'line',{x1:x,x2:x,y1:30,y2:210,class:'analysis-cursor','data-combat-cursor':''});
  series.forEach(([key,,css])=>{if(shown.has(key)&&p[key]!=null)svg(chart,'circle',{cx:x,cy:combatScale.y(p[key]),r:4,class:`combat-series-point combat-${css}`,'data-combat-cursor':''});});
  if(p.row)details($('[data-combat-detail]'),p.row);
  else $('[data-combat-detail]').replaceChildren(node('span',`${p.label} · 各指标按各自有效对局求均值`),node('strong',series.map(([k,label])=>`${label} ${fmt(p[k])}（${p.counts[k]}/${p.games}局）`).join(' / ')));
  chart.setAttribute('aria-label',`${p.label}，${series.map(([k,label])=>`${label} ${fmt(p[k])}`).join('，')}；左右键切换，回车查看对局`);
 }
 function drawCombat(){
  const chart=$('[data-combat-chart]');if(chart.closest('[data-view-panel]').hidden)return;
  const w=Math.max(300,Math.round(chart.getBoundingClientRect().width));chart.setAttribute('viewBox',`0 0 ${w} 270`);
  const daily=$('[data-combat-grain]').value==='day';
  if(!daily)combatPoints=rows.map((r,i)=>({...r,row:r,label:`第 ${i+1} 局`}));
  else{
   const days=new Map();rows.forEach(r=>{if(!/^\d{4}-\d{2}-\d{2}/.test(r.when))return;const day=r.when.slice(0,10);if(!days.has(day))days.set(day,[]);days.get(day).push(r);});
   combatPoints=Array.from(days,([label,games])=>{const p={label,games:games.length,counts:{}};series.forEach(([k])=>{const vs=values(games,k);p[k]=avg(vs);p.counts[k]=vs.length;});return p;});
  }
  const vs=combatPoints.flatMap(p=>Array.from(shown,k=>p[k])).filter(v=>v!=null);
  $('[data-combat-coverage]').textContent=series.map(([k,label])=>`${label} ${values(rows,k).length}/${rows.length} 局`).join(' · ')+(daily?' · 有对局日期等距排列':' · 由早到晚');
  if(!vs.length){combatScale=null;empty(chart,w,shown.size?'没有有效的战斗读数':'请选择至少一项指标');$('[data-combat-detail]').textContent='调整上方筛选或图例，查看战斗变化。';return;}
  combatScale=axes(chart,w,270,[0,Math.max(1,combatPoints.length-1)],vs,daily?'有对局的日期':'按对局顺序','次数', [combatPoints[0].label,combatPoints[combatPoints.length-1].label]);
  series.forEach(([key,,css])=>{
   if(!shown.has(key))return;let part=[];
   const flush=()=>{if(part.length>1)svg(chart,'polyline',{points:part.join(' '),class:`combat-series-line combat-${css}`});else if(part.length){const[cx,cy]=part[0].split(',');svg(chart,'circle',{cx,cy,r:3,class:`combat-series-point combat-${css}`});}part=[];};
   combatPoints.forEach((p,i)=>{if(p[key]==null)flush();else part.push(`${combatScale.x(i)},${combatScale.y(p[key])}`);});flush();
  });
  selectCombat(Math.min(combatAt??(combatPoints.length-1),combatPoints.length-1));
 }
 $('[data-combat-grain]').addEventListener('change',drawCombat);
 root.querySelectorAll('[data-combat-series]').forEach(b=>b.addEventListener('click',()=>{const k=b.dataset.combatSeries;shown.has(k)?shown.delete(k):shown.add(k);b.setAttribute('aria-pressed',String(shown.has(k)));drawCombat();}));
 $('[data-combat-chart]').addEventListener('pointermove',e=>{const rect=e.currentTarget.getBoundingClientRect(),w=Math.max(300,Math.round(rect.width));selectCombat(Math.round(((e.clientX-rect.left)/rect.width*w-54)/(w-80)*Math.max(1,combatPoints.length-1)));});
 function keyboard(chart,getIndex,getPoints,show){chart.addEventListener('keydown',e=>{
  if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();show(e.key==='Home'?0:e.key==='End'?getPoints().length-1:getIndex()+(e.key==='ArrowLeft'?-1:1));}
  if(e.key==='Enter'){const p=getPoints()[getIndex()],r=p&&(p.row||p);if(r&&r.id!=null)link(r).click();}
 });}
 keyboard($('[data-combat-chart]'),()=>combatAt,()=>combatPoints,selectCombat);

 // Four independent encodings: both coordinates, bubble area, category color.
 let scatter=[],scatterAt=null;
 const palette=['var(--combat-kills)','var(--combat-assists)','var(--combat-knocks)','var(--p2-bg)','var(--good)','var(--muted)'];
 const control=name=>$(`[data-scatter-${name}]`);
 ['x','y','size'].forEach(name=>{if(name==='size'){const opt=node('option','等大');opt.value='none';control(name).append(opt);}Object.entries(metrics).filter(([key])=>name!=='size'||key!=='rp_delta').forEach(([key,m])=>{const opt=node('option',m.label);opt.value=key;control(name).append(opt);});control(name).addEventListener('change',()=>{root.querySelectorAll('[data-scatter-preset]').forEach(b=>b.setAttribute('aria-pressed','false'));drawScatter();});});
 control('color').addEventListener('change',drawScatter);
 const presets={pressure:['damage','knockdowns','assists','placement'],involvement:['knockdowns','ka','damage','placement'],survival:['survival_time','ka','knockdowns','placement'],ranked:['ka','rp_delta','damage','rp_delta'],team:['damage_share','kill_participation','knockdowns','placement']};
 function preset(key){['x','y','size','color'].forEach((n,i)=>{control(n).value=presets[key][i];});root.querySelectorAll('[data-scatter-preset]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.scatterPreset===key)));drawScatter();}
 root.querySelectorAll('[data-scatter-preset]').forEach(b=>b.addEventListener('click',()=>preset(b.dataset.scatterPreset)));
 function selectScatter(index){
  if(!scatter.length)return;scatterAt=Math.max(0,Math.min(scatter.length-1,index));const r=scatter[scatterAt],chart=$('[data-scatter]');
  chart.querySelectorAll('circle').forEach((n,i)=>n.classList.toggle('selected',i===scatterAt));
  details($('[data-scatter-detail]'),r);
  const extra=node('span',`每存活分钟伤害 ${fmt(r.damage_per_min)} · K+A ${fmt(r.ka)} · 击杀参与率 ${r.kill_participation==null?'—':fmt(r.kill_participation)+'%'} · 伤害占比 ${r.damage_share==null?'—':fmt(r.damage_share)+'%'}${r.squad_source?' · '+r.squad_source:''}`);
  $('[data-scatter-detail]').append(extra);
  chart.setAttribute('aria-label',`${context(r)}，${numbers(r)}；左右键切换，回车查看对局`);
 }
 function drawScatter(){
  const chart=$('[data-scatter]');if(chart.closest('[data-view-panel]').hidden)return;
  const xkey=control('x').value,ykey=control('y').value,size=control('size').value,color=control('color').value;
  const all=rows.filter(r=>r[xkey]!=null&&r[ykey]!=null&&(size==='none'||r[size]!=null)&&(color==='legend'||r[color]!=null));
  scatter=all.length<=600?all:Array.from({length:600},(_,i)=>all[Math.round(i*(all.length-1)/599)]);
  const w=Math.max(300,Math.round(chart.getBoundingClientRect().width)),h=310;chart.setAttribute('viewBox',`0 0 ${w} ${h}`);
  $('[data-scatter-note]').textContent=`四项编码完整 ${all.length} / ${rows.length} 局${all.length>600?' · 等距抽样 600 点':''}`;
  const key=$('[data-scatter-key]');key.replaceChildren();
  if(!all.length){empty(chart,w,'当前组合的完整读数不足');$('[data-scatter-detail]').textContent='调整坐标、气泡或颜色编码，也可切换预设。缺失值不作零。';return;}
  const {x,y}=axes(chart,w,h,all.map(r=>r[xkey]),all.map(r=>r[ykey]),metrics[xkey].label,metrics[ykey].label);
  const legends=Array.from(new Set(all.map(r=>r.legend))).sort((a,b)=>a.localeCompare(b,'zh-CN'));
  const category=r=>color==='legend'?(legends.indexOf(r.legend)<5?r.legend:'其他传奇'):color==='rp_delta'?(r.rp_delta>0?'RP 正':r.rp_delta<0?'RP 负':'RP 持平'):(r.placement===1?'冠军':r.placement<=5?'第 2–5':r.placement<=10?'第 6–10':'第 11 名及以后');
  const labels=color==='legend'?[...legends.slice(0,5),...(legends.length>5?['其他传奇']:[])]:color==='rp_delta'?['RP 正','RP 负','RP 持平']:['冠军','第 2–5','第 6–10','第 11 名及以后'];
  const colors=color==='rp_delta'?['var(--good)','var(--bad)','var(--muted)']:palette;
  labels.forEach((label,i)=>{const tag=node('span'),swatch=node('i');swatch.style.background=colors[i];tag.append(swatch,node('span',label));key.append(tag);});
  const max=size==='none'?1:all.reduce((v,r)=>Math.max(v,Math.max(0,r[size])),1);
  scatter.forEach((r,i)=>{
   const radius=size==='none'?4:Math.max(2,13*Math.sqrt(Math.max(0,r[size])/max));
   const circle=svg(chart,'circle',{cx:x(r[xkey]),cy:y(r[ykey]),r:radius,class:`analysis-point combat-bubble${r.review?' review':''}`,style:`--bubble-color:${colors[labels.indexOf(category(r))]}`});
   svg(circle,'title',{},`${context(r)} · ${numbers(r)} · ${metrics[xkey].label} ${fmt(r[xkey])} · ${metrics[ykey].label} ${fmt(r[ykey])}${size==='none'?'':' · 气泡 '+metrics[size].label+' '+fmt(r[size])}`);
   circle.addEventListener('pointerenter',()=>selectScatter(i));circle.addEventListener('click',()=>selectScatter(i));
  });
  key.append(node('span',size==='none'?'气泡等大':`面积随${metrics[size].label}变化，零值保留最小点`,'combat-size-note'));
  selectScatter(Math.min(scatterAt??(scatter.length-1),scatter.length-1));
 }
 keyboard($('[data-scatter]'),()=>scatterAt,()=>scatter,selectScatter);
 preset('pressure');

 // Multi-metric group comparison. Each column retains its own denominator.
 const profileFields=['kills','assists','knockdowns','damage','ka','damage_per_min','ka_per10','knocks_per10','kill_participation','damage_share','survival_time','rp_delta','revives','respawns'];
 let groupDimension='legend',groupQuery='',profileSort='games',profileAsc=false;
 function profile(){
  const groups=new Map();rows.forEach(r=>{const name=r[groupDimension];if(!groups.has(name))groups.set(name,[]);groups.get(name).push(r);});
  const data=Array.from(groups,([name,rs])=>{const g={name,games:rs.length,counts:{}};profileFields.forEach(k=>{const vs=values(rs,k);g[k]=avg(vs);g.counts[k]=vs.length;});return g;}).filter(g=>g.name.toLocaleLowerCase().includes(groupQuery));
  data.sort((a,b)=>{if(a[profileSort]==null)return b[profileSort]==null?0:1;if(b[profileSort]==null)return-1;return(a[profileSort]-b[profileSort])*(profileAsc?1:-1)||a.name.localeCompare(b.name,'zh-CN');});
  const head=$('[data-profile-head]'),tr=node('tr');head.replaceChildren(tr);tr.append(node('th','分组'));
  ['games',...profileFields].forEach(k=>{const th=node('th'),b=node('button',k==='games'?'局数':metrics[k].label);b.type='button';if(k===profileSort)th.setAttribute('aria-sort',profileAsc?'ascending':'descending');b.addEventListener('click',()=>{profileAsc=profileSort===k?!profileAsc:false;profileSort=k;profile();});th.scope='col';th.append(b);tr.append(th);});
  const max={};profileFields.forEach(k=>{max[k]=data.reduce((m,g)=>Math.max(m,Math.abs(g[k]||0)),1);});
  const body=$('[data-profile-body]');body.replaceChildren();data.forEach(g=>{
   const row=node('tr'),th=node('th',g.name);th.scope='row';if(g.games<5)th.append(node('small','少于5局','combat-low-sample'));row.append(th,node('td',g.games));
   profileFields.forEach(k=>{const cell=node('td',null,'combat-profile-cell');cell.style.setProperty('--cell-strength',String(Math.abs(g[k]||0)/max[k]*.20));cell.title=`${metrics[k].label} ${fmt(g[k],metrics[k].digits)}；有效 ${g.counts[k]} / ${g.games} 局`;cell.append(node('strong',fmt(g[k],metrics[k].digits)),node('small',`${g.counts[k]}/${g.games}`));row.append(cell);});body.append(row);
  });
  if(!data.length){const row=node('tr'),td=node('td','没有匹配的分组');td.colSpan=profileFields.length+2;row.append(td);body.append(row);}
  $('[data-profile-note]').textContent=`${data.length} 个分组 · 每列独立着色（RP按绝对值，正负见数值），不代表综合评分。每格下方为有效/总局数；存活量按秒计。跨模式、段位与赛季比较请结合筛选。`;
 }
 $('[data-profile-toggle]').addEventListener('change',e=>{
  $('[data-profile-wrap]').hidden=!e.target.checked;$('[data-profile-note]').hidden=!e.target.checked;$('[data-single-table]').hidden=e.target.checked;$('[data-single-foot]').hidden=e.target.checked;
  $('[data-metric]').closest('label').hidden=e.target.checked;
 });
 root.addEventListener('analysis-group',e=>{groupDimension=e.detail.dimension;groupQuery=e.detail.query;profile();});

 // Outcome matrix: bins describe the sample, not an RP scoring model.
 const kaBands=[[0,0],[1,2],[3,5],[6,Infinity]],placeBands=[['冠军',1,1],['第 2–5',2,5],['第 6–10',6,10],['第 11–20',11,20]];
 let outcomeSelection=null,outcomeLimit=20;
 function outcomeMatches(items,label){
  const target=$('[data-outcome-detail]');target.replaceChildren(node('h4',`${label} · ${items.length} 局`));
  const wrap=node('div',null,'table-wrap'),table=node('table',null,'analysis-table'),head=node('thead'),hr=node('tr');
  ['对局','传奇','地图','击杀','助攻','击倒','伤害','名次','RP','明细'].forEach(t=>hr.append(node('th',t)));head.append(hr);table.append(head);const body=node('tbody');
  items.slice(0,outcomeLimit).forEach(r=>{const tr=node('tr');tr.append(node('td',r.when+(r.review?' · 待确认':'')),node('td',r.legend),node('td',r.map));['kills','assists','knockdowns','damage','placement','rp_delta'].forEach(k=>tr.append(node('td',fmt(r[k],0))));const td=node('td');td.append(link(r));tr.append(td);body.append(tr);});table.append(body);wrap.append(table);target.append(wrap);
  if(items.length>outcomeLimit){const more=node('button',`再显示 ${Math.min(20,items.length-outcomeLimit)} 局`,'analysis-button');more.type='button';more.addEventListener('click',()=>{outcomeLimit+=20;outcomeMatches(items,label);});target.append(more);}
 }
 function outcomes(){
  const key=$('[data-outcome-metric]').value,eligible=rows.filter(r=>r.ka!=null&&r.placement!=null&&r.placement>=1&&r.placement<=20),all=values(eligible,key),max=all.reduce((v,n)=>Math.max(v,Math.abs(n)),1);
  $('[data-outcome-note]').textContent=`K+A及名次完整 ${eligible.length}/${rows.length} 局 · ${metrics[key].label}有效 ${all.length} 局`;
  const body=$('[data-outcomes]');body.replaceChildren();
  placeBands.forEach(([label,lo,hi],ri)=>{const tr=node('tr'),th=node('th',label);th.scope='row';tr.append(th);
   kaBands.forEach(([kl,kh],ci)=>{
    const items=eligible.filter(r=>r.placement>=lo&&r.placement<=hi&&r.ka>=kl&&r.ka<=kh),vs=values(items,key),mean=avg(vs),td=node('td'),button=node('button');button.type='button';button.disabled=!items.length;
    button.style.setProperty('--heat-color',key==='rp_delta'?(mean>=0?'var(--good)':'var(--bad)'):'var(--combat-assists)');button.style.setProperty('--heat-strength',mean==null?'0':String(.05+Math.abs(mean)/max*.28));
    button.append(node('strong',fmt(mean,metrics[key].digits)),node('small',`${vs.length}/${items.length} 局`));button.title=`${label}，K+A ${ci===3?'6+':kl===kh?kl:kl+'–'+kh}；${metrics[key].label} ${fmt(mean)}；有效 ${vs.length}/${items.length} 局`;
    button.setAttribute('aria-pressed',String(outcomeSelection===`${ri}:${ci}`));button.addEventListener('click',()=>{outcomeSelection=`${ri}:${ci}`;outcomeLimit=20;outcomes();outcomeMatches(items,`${label} · K+A ${ci===3?'6+':kl===kh?kl:kl+'–'+kh}`);});td.append(button);tr.append(td);
   });body.append(tr);
  });
 }
 $('[data-outcome-metric]').addEventListener('change',()=>{outcomeSelection=null;outcomes();$('[data-outcome-detail]').replaceChildren();});
 root.addEventListener('analysis-view',e=>{if(e.detail==='combat')drawCombat();if(e.detail==='scatter')drawScatter();});
 root.addEventListener('analysis-resize',()=>{drawCombat();drawScatter();});
 kpis();drawCombat();profile();outcomes();
})();
