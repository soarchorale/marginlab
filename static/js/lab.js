'use strict';
const $ = id => document.getElementById(id);
const fmt = (v,d=3) => v == null || !Number.isFinite(Number(v)) ? '—' : Number(v).toFixed(d);
const NS='http://www.w3.org/2000/svg';
let researchData=null, requestId=0, controller=null;
let researchPending=false, renderedRequest=null, renderedPayload=null;
function researchParams(){
 const body={};
 ['symbol','period','source','scenario','window','horizon','correlation','band','gap','lookback_days','start','end'].forEach(k=>body[k]=$(k).value);
 return body;
}
// Transport sync timestamps do not change the visible market data.
const payloadKey=data=>JSON.stringify(data,(key,value)=>key==='synced_at'?undefined:value);
const colors=['#198c80','#d48e50','#729bc4','#b4bec5'];
function el(tag,attrs={},text){const n=document.createElementNS(NS,tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));if(text!=null)n.textContent=text;return n;}
function add(svg,tag,attrs,text){const n=el(tag,attrs,text);svg.append(n);return n;}
async function api(url,options={}){const res=await window.marginFetch(url,options);const data=await res.json();if(!res.ok)throw new Error(data.error||'请求失败');return data;}
function chart(id,series,options={}){
 const container=$(id); container.replaceChildren(); const w=Math.max(container.clientWidth,300),h=container.clientHeight;
 const svg=el('svg',{viewBox:`0 0 ${w} ${h}`,role:'img','aria-label':options.label||id});container.append(svg);
 const pad={l:48,r:options.dual?45:20,t:23,b:38},pw=w-pad.l-pad.r,ph=h-pad.t-pad.b;
 const valid=series.flatMap(s=>s.data.filter(d=>d[0]!=null&&d[1]!=null&&Number.isFinite(d[0])&&Number.isFinite(d[1])));
 if(!valid.length){add(svg,'text',{x:w/2,y:h/2,'text-anchor':'middle'},'样本不足，等待有效观测');return;}
 const range=(xs,fixed)=>{xs=xs.filter(Number.isFinite);if(!xs.length)xs=[0,1];let a=fixed?.[0]??Math.min(...xs),b=fixed?.[1]??Math.max(...xs);if(a===b){a-=Math.abs(a)*.02||1;b+=Math.abs(b)*.02||1;}return [a,b];};
 const xr=range(valid.map(d=>d[0]),options.xRange);
 const yr=range(series.filter(s=>!s.right).flatMap(s=>s.data.filter(d=>d[1]!=null).map(d=>d[1])),options.yRange);
 const yright=options.dual?range(series.filter(s=>s.right).flatMap(s=>s.data.filter(d=>d[1]!=null).map(d=>d[1]))):yr;
 const X=x=>pad.l+(x-xr[0])/(xr[1]-xr[0])*pw;
 const Y=(y,right=false)=>{const r=right?yright:yr;return pad.t+(1-(y-r[0])/(r[1]-r[0]))*ph;};
 for(let i=0;i<=4;i++){
  const y=pad.t+i*ph/4;add(svg,'line',{x1:pad.l,y1:y,x2:w-pad.r,y2:y,stroke:'#edf0f2','stroke-dasharray':'3 4'});
  add(svg,'text',{x:pad.l-9,y:y+3,'text-anchor':'end'},fmt(yr[1]-(yr[1]-yr[0])*i/4,options.decimals??2));
  if(options.dual)add(svg,'text',{x:w-pad.r+8,y:y+3},fmt(yright[1]-(yright[1]-yright[0])*i/4,1));
 }
 for(let i=0;i<=4;i++){const x=xr[0]+(xr[1]-xr[0])*i/4;add(svg,'text',{x:X(x),y:h-13,'text-anchor':'middle'},options.xLabel?options.xLabel(x):fmt(x,3));}
 if(options.band){const [lo,hi]=options.band;const a=Math.max(pad.l,X(lo)),b=Math.min(w-pad.r,X(hi));if(b>a)add(svg,'rect',{x:a,y:pad.t,width:b-a,height:ph,fill:'#118c8009',stroke:'#b8d9d1','stroke-dasharray':'4 4'});}
 if(yr[0]<0&&yr[1]>0)add(svg,'line',{x1:pad.l,x2:w-pad.r,y1:Y(0),y2:Y(0),stroke:'#ced8dc'});
 series.forEach((s,j)=>{
  if(s.gradient){for(let i=1;i<s.data.length;i++){const a=s.data[i-1],b=s.data[i];if(a[1]==null||b[1]==null)continue;add(svg,'line',{x1:X(a[0]),y1:Y(a[1]),x2:X(b[0]),y2:Y(b[1]),stroke:`hsl(169 37% ${80-i/s.data.length*51}%)`,'stroke-width':1.5});}}
  else {let path='',gap=true;s.data.forEach(d=>{if(d[1]==null||!Number.isFinite(d[1])){gap=true;return;}path+=`${gap?'M':'L'}${X(d[0]).toFixed(2)},${Y(d[1],s.right).toFixed(2)} `;gap=false;});add(svg,'path',{d:path,fill:'none',stroke:s.color||colors[j%4],'stroke-width':s.dash?1:1.8,'stroke-dasharray':s.dash?'5 4':'none'});}
  if(s.points)s.data.filter(d=>d[1]!=null).forEach(d=>add(svg,'circle',{cx:X(d[0]),cy:Y(d[1],s.right),r:3.6,fill:s.color||colors[j%4],stroke:'#fff','stroke-width':1.5}));
 });
 if(options.legend)options.legend.forEach((name,i)=>add(svg,'text',{x:pad.l+i*130,y:12,style:`fill:${colors[i]}`},'● '+name));
 const line=add(svg,'line',{x1:0,x2:0,y1:pad.t,y2:h-pad.b,stroke:'#93ada6','stroke-dasharray':'3 3',visibility:'hidden'});
 svg.addEventListener('pointermove',e=>{const rect=svg.getBoundingClientRect();const px=(e.clientX-rect.left)*w/rect.width;const val=xr[0]+(px-pad.l)/pw*(xr[1]-xr[0]);let nearest=valid.reduce((a,b)=>Math.abs(a[0]-val)<Math.abs(b[0]-val)?a:b);line.setAttribute('x1',X(nearest[0]));line.setAttribute('x2',X(nearest[0]));line.setAttribute('visibility','visible');const tip=$('chart-tooltip');tip.textContent=options.tooltip?options.tooltip(nearest[0],nearest):series.filter(s=>s.data.length).map(s=>{const d=s.data.reduce((a,b)=>Math.abs(a[0]-val)<Math.abs(b[0]-val)?a:b);return s.name+': '+fmt(d[1]);}).join('\n');tip.hidden=false;tip.style.left=Math.max(8,Math.min(e.clientX+15,window.innerWidth-230))+'px';tip.style.top=Math.max(8,Math.min(e.clientY+15,window.innerHeight-130))+'px';});
 svg.addEventListener('pointerleave',()=>{line.setAttribute('visibility','hidden');$('chart-tooltip').hidden=true;});
}
function td(row,value,className=''){const cell=document.createElement('td');cell.textContent=value??'—';cell.className=className;row.append(cell);}
function renderResearch(){
 if(!researchData)return;const d=researchData,b=d.bars,last=b.at(-1),visits=d.visits;
 const cache=d.meta.cache;const notice=$('cache-status');notice.hidden=!cache;
 if(cache){notice.classList.toggle('stale',cache.stale);notice.textContent=(cache.stale?'行情刷新未完成，正在显示历史缓存。':cache.refreshed?'行情已更新并保存。':'正在读取本地缓存。')+' 最近成功同步：'+(cache.last_success?.replace('T',' ').slice(0,19)||'尚无')+'；最新行情：'+d.meta.asof.slice(0,10)+'；保留 '+cache.retention_months+' 个月。'+(cache.range_clipped?'查询范围已限制在保留期限内。':'');}
 $('metric-price').textContent=fmt(last.close);$('metric-vix').textContent=fmt(last.vix,2);$('metric-corr').textContent=fmt(last.corr_return);$('level-corr').textContent='价格水平相关性 '+fmt(last.corr_level);$('metric-drift').textContent=fmt(last.accumulated==null?null:last.accumulated*100,2);$('metric-state').textContent=last.state;$('metric-date').textContent=last.timestamp.replace('T',' ').slice(0,16);$('symbol-mini').textContent=d.meta.symbol;
 $('source-badge').textContent=d.meta.source==='demo'?'演示数据 · 非实盘':d.meta.source==='csv'?'CSV 导入 · 用户数据':'TqSdk · 分钟行情';$('data-range').textContent=b[0].timestamp.slice(0,10)+' — '+last.timestamp.slice(0,10)+' / '+b.length+' 根';
 const s=(key,name,color,mult=1)=>({name,color,data:b.map((r,i)=>[i,r[key]==null?null:r[key]*mult])});
 const dateLabel=x=>b[Math.max(0,Math.min(b.length-1,Math.round(x)))]?.timestamp.replace('T',' ').slice(5,d.meta.period==='daily'?10:16);
 const opts={xLabel:dateLabel,xRange:[0,b.length-1]};
 chart('market-chart',[s('close','标的',colors[0]),{...s('vix','MVIX',colors[1]),right:true,points:true}],{...opts,dual:true,decimals:3,tooltip:x=>{const r=b[Math.round(x)];return r.timestamp.replace('T',' ').slice(0,16)+'\n标的 '+fmt(r.close)+'\nMVIX '+fmt(r.vix,2);}});
 chart('phase-chart',[{name:'轨迹',gradient:true,data:b.map(r=>[r.close,r.vix])},{name:'独立回访',points:true,color:'#213e3a',data:visits.map(r=>[r.price,r.vix])}],{band:[d.anchor.low,d.anchor.high],tooltip:(x,n)=>'标的 '+fmt(x,4)+'\nMVIX '+fmt(n[1],2)});
 chart('residual-chart',[s('accumulated','累计残差',colors[0],100),{...s('upper','历史 95%',colors[1],100),dash:true},{...s('lower','历史 5%',colors[2],100),dash:true}],opts);
 chart('correlation-chart',[s('corr_return','涨跌幅',colors[0]),s('corr_level','价格水平',colors[1])],{...opts,yRange:[-1,1],legend:['涨跌幅相关性','价格水平相关性']});
 chart('hv-chart',[s('hv','历史波动率',colors[0]),s('vix','MVIX',colors[1])],{...opts,legend:['历史波动率 %','MVIX 指数点']});
 chart('sensitivity-chart',[s('vanna','Vanna',colors[0]),{...s('volga','Volga',colors[1]),right:true}],{...opts,dual:true,legend:['Vanna · 左轴','Volga · 右轴']});
 chart('visits-chart',['上行进入','下行进入'].map((dir,i)=>({name:dir,color:colors[i],points:true,data:visits.filter(v=>v.direction===dir).map(v=>[v.index,v.vix])})),{...opts,legend:['上行进入','下行进入']});
 $('anchor-label').textContent=`锚点 ${fmt(d.anchor.price,4)} / 价格带 ${fmt(d.anchor.low,4)}–${fmt(d.anchor.high,4)}`;$('visit-count').textContent=visits.length+' 次回访';$('visits-body').replaceChildren();
 visits.slice().reverse().forEach(v=>{const row=document.createElement('tr');[v.timestamp.slice(0,16),v.direction,fmt(v.price,4),fmt(v.vix,2),v.matches,fmt(v.drift==null?null:v.drift*100,3),v.state].forEach((a,i)=>td(row,a,i===5?(v.drift>0?'positive':'negative'):''));$('visits-body').append(row);});
 if(!visits.length){const row=document.createElement('tr');td(row,'尚无符合规则的独立回访');$('visits-body').append(row);}
}
async function loadResearch(e,{background=false}={}){
 e?.preventDefault();
 const body=researchParams(),query=JSON.stringify(body);
 // Do not submit unfinished form edits or overlap an active request from a timer.
 if(background&&(researchPending||query!==renderedRequest))return;
 const id=++requestId;controller?.abort();controller=new AbortController();researchPending=true;
 if(!background){
  $('error').hidden=true;$('loading').hidden=false;$('refresh').disabled=true;
  $('research-content').classList.add('muted-content');$('export').disabled=true;
 }
 try{
  if(body.source==='csv'){const file=$('csv-file').files[0];if(!file)throw new Error('请先选择包含 timestamp,open,high,low,close,vix 的 CSV');body.csv=await file.text();}
  const data=await api('/api/research',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal});
  if(id!==requestId||(background&&query!==JSON.stringify(researchParams())))return;
  const key=payloadKey(data);
  // Keep chart nodes and hover state intact when a poll returns unchanged data.
  if(!background||key!==renderedPayload||$('research-content').classList.contains('muted-content')){
   if(data.source==='tqsdk')renderTq(data);
   else{marketData=null;researchData=data;renderResearch();$('research-content').classList.remove('muted-content');$('export').disabled=false;}
  }
  renderedRequest=query;renderedPayload=key;$('error').hidden=true;
 }catch(error){
  if(id!==requestId||error.name==='AbortError')return;
  $('error').textContent=error.message+(background?'。自动刷新失败，保留上次成功的数据。':'。下方旧结果已置灰，不代表本次参数。');$('error').hidden=false;
 }finally{
  if(id===requestId){researchPending=false;if(!background){$('loading').hidden=true;$('refresh').disabled=false;}}
 }
}
$('research-form')?.addEventListener('submit',loadResearch);
$('source')?.addEventListener('change',()=>{const source=$('source').value;$('csv-label').hidden=source!=='csv';$('scenario-label').hidden=source!=='demo';$('tq-market').hidden=source!=='live';$('mvix-history-panel').hidden=source!=='live';$('mvix-summary').textContent='指数点 · 非单一期权 IV';});
$('export')?.addEventListener('click',()=>{if(!researchData)return;const blob=new Blob([JSON.stringify(researchData,null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='margin-research-'+researchData.meta.source+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);});
async function loadPricing(e){e?.preventDefault();$('pricing-error').textContent='';const f=new FormData($('pricing-form'));const params=new URLSearchParams({S:f.get('S'),K:f.get('K'),t:Number(f.get('days'))/365,v:Number(f.get('vol'))/100,r:Number(f.get('rate'))/100,q:Number(f.get('yield'))/100,type:f.get('type')});try{const data=await api('/api/stress?'+params);$('greek-cards').replaceChildren();Object.entries(data.greeks).forEach(([k,v])=>{const card=document.createElement('article'),name=document.createElement('div'),num=document.createElement('strong');name.textContent=k;num.textContent=fmt(v,5);card.append(name,num);$('greek-cards').append(card);});const table=document.createElement('table'),head=document.createElement('tr');td(head,'Δσ / ΔS');const xs=[...new Set(data.scenarios.map(r=>r.price_change))],ys=[...new Set(data.scenarios.map(r=>r.vol_change))].reverse();xs.forEach(x=>td(head,(x*100).toFixed(0)+'%'));table.append(head);const max=Math.max(...data.scenarios.map(r=>Math.abs(r.pnl)),.000001);ys.forEach(y=>{const row=document.createElement('tr');td(row,(y*100).toFixed(0)+' pp');xs.forEach(x=>{const v=data.scenarios.find(r=>r.price_change===x&&r.vol_change===y);const cell=document.createElement('td');cell.textContent=fmt(v.pnl,4);cell.style.background=v.pnl>0?`rgba(208,139,77,${.08+.55*Math.abs(v.pnl)/max})`:`rgba(29,148,132,${.08+.55*Math.abs(v.pnl)/max})`;cell.title=`精确价格 ${fmt(v.exact,6)}\n二阶近似 ${fmt(v.approximation,6)}\n近似误差 ${fmt(v.approximation-v.exact,6)}`;row.append(cell);});table.append(row);});$('stress-grid').replaceChildren(table);}catch(e){$('pricing-error').textContent=e.message;}}
$('pricing-form')?.addEventListener('submit',loadPricing);
$('market-form')?.addEventListener('submit',async e=>{e.preventDefault();$('tool-error').textContent='';$('tool-result').textContent='正在查询…';const tool=$('tool').value,params=new URLSearchParams({symbol:$('tool-symbol').value,start:$('tool-start').value,end:$('tool-end').value});if(tool==='futures'){params.set('start',params.get('start').replaceAll('-',''));params.set('end',params.get('end').replaceAll('-',''));}const button=e.submitter;button.disabled=true;try{const rows=await api((['futures','rank'].includes(tool)?'/api/'+tool:'/api/options/'+tool)+'?'+params);$('tool-result').replaceChildren();if(!rows.length){$('tool-result').textContent='此区间没有符合条件的数据';return;}const table=document.createElement('table'),head=document.createElement('tr'),keys=Object.keys(rows[0]);keys.forEach(k=>{const cell=document.createElement('th');cell.textContent=k;head.append(cell);});table.append(head);rows.forEach(r=>{const row=document.createElement('tr');keys.forEach(k=>td(row,typeof r[k]==='number'?fmt(r[k],4):r[k]));table.append(row);});$('tool-result').append(table);}catch(e){$('tool-result').textContent='没有可用结果';$('tool-error').textContent=e.message;}finally{button.disabled=false;}});
$('tool')?.addEventListener('change',()=>{$('tool-symbol').value=$('tool').value==='futures'?'LH0':'50ETF';});
const today=new Date();
const iso=d=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(d);
if ($('end')) $('end').value=iso(today);
if ($('tool-end')) $('tool-end').value=iso(today);
if ($('tool-start')) $('tool-start').value=iso(new Date(today.getTime()-7*86400000));
let resizeTimer;
window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(marketData&&$('source')?.value==='live')renderTq(marketData);else if($('research'))renderResearch();},150);});
if (document.body.dataset.page==='research'){loadResearch();setInterval(()=>{if(!document.hidden&&$('source').value==='live'&&!researchPending)loadResearch(null,{background:true});},15000);}
if (document.body.dataset.page==='pricing') loadPricing();

let marketData=null;
function candleChart(id,bars){
 const box=$(id);box.replaceChildren();if(!bars.length){box.textContent='等待 TqSdk 分钟行情…';return;}
 const w=Math.max(box.clientWidth,300),h=box.clientHeight,svg=el('svg',{viewBox:`0 0 ${w} ${h}`,role:'img','aria-label':'期货 OHLC K 线'});box.append(svg);
 const b=bars.slice(-160),lo=Math.min(...b.map(x=>x.low)),hi=Math.max(...b.map(x=>x.high)),range=hi-lo||1;
 const X=i=>48+i*(w-68)/b.length,Y=p=>20+(hi-p)/range*(h-50),bw=Math.max(1,(w-68)/b.length*.65);
 for(let i=0;i<5;i++){const price=lo+range*i/4;add(svg,'text',{x:4,y:Y(price)+3},fmt(price,1));add(svg,'line',{x1:45,x2:w-10,y1:Y(price),y2:Y(price),stroke:'#edf0f2'});}
 b.forEach((r,i)=>{const color=r.close>=r.open?'#d48e50':'#198c80';add(svg,'line',{x1:X(i),x2:X(i),y1:Y(r.high),y2:Y(r.low),stroke:color});const body=add(svg,'rect',{x:X(i)-bw/2,y:Math.min(Y(r.open),Y(r.close)),width:bw,height:Math.max(1,Math.abs(Y(r.open)-Y(r.close))),fill:color});body.append(el('title',{},`${r.timestamp}\n开 ${r.open} 高 ${r.high} 低 ${r.low} 收 ${r.close}\n成交量 ${r.volume}`));});
 add(svg,'text',{x:48,y:h-8},b[0].timestamp.replace('T',' ').slice(5,16));add(svg,'text',{x:w-10,y:h-8,'text-anchor':'end'},b.at(-1).timestamp.replace('T',' ').slice(5,16));
}
function renderTq(d){
 marketData=d;$('tq-market').hidden=false;$('research-content').classList.remove('muted-content');$('export').disabled=true;
 $('source-badge').textContent='TqSdk · 股指行情';$('tq-cards').replaceChildren();
 d.cards.forEach(c=>{const a=document.createElement('article');const label=document.createElement('div'),v=document.createElement('strong'),note=document.createElement('small');label.textContent=c.name+' · '+c.contract;v.textContent=fmt(c.future_price,1);note.textContent='基差 '+fmt(c.future_price==null||c.index_price==null?null:c.future_price-c.index_price,1)+' · '+(c.quote_time||'');a.append(label,v,note);$('tq-cards').append(a);});
 const status=d.status;$('future-title').textContent=(status.name||'股指期货')+' · '+(status.contract||'正在解析主力');
 $('tq-status').textContent=(d.collector.error||({'connecting':'正在连接并订阅行情，首次加载可能需要数十秒','streaming':'行情订阅已连接','scheduled':'自动采集已启用，等待工作日 09:20–15:10','idle':'行情采集待启动','shared':'其他进程正在采集行情'}[d.collector.state]||d.collector.state))+'。最新行情：'+(status.quote_time||'尚无')+'。历史保留 '+d.cache.retention_months+' 个月；'+(d.collector.automatic?'后台自动采集四组行情，不依赖页面打开或登录。':'按页面需求采集，关闭页面后停止续订。');
 candleChart('future-chart',d.future_bars);
 const f=d.future_bars;chart('oi-chart',[{name:'成交量',data:f.map((r,i)=>[i,r.volume])},{name:'持仓量',right:true,data:f.map((r,i)=>[i,r.close_oi])}],{dual:true,xLabel:i=>f[Math.round(i)]?.timestamp.slice(11,16)||''});
 const m=d.mvix,valid=d.mvix_latest_valid??(m?.quality==='valid'?m:null),cache=d.mvix_cache;
 const mvixCaption=valid?`SQLite · ${valid.timestamp.replace('T',' ')} · ${cache?.valid_count??1} 个有效快照`:'所选区间暂无有效快照';
 $('mvix-history-panel').hidden=false;
 $('mvix-history-note').textContent=mvixCaption+(cache?.valid_count===1?'。目前只有一个点，尚不能形成历史曲线或漂移信号。':'。缺失报价保留为空，不跨缺口连接。');
 if(d.mvix_backfill?.limitation)$('mvix-history-note').textContent+=' '+d.mvix_backfill.limitation+'。';
 const history=d.mvix_history||[];
 chart('mvix-history-chart',[{name:'MVIX',color:colors[1],points:true,data:history.map((r,i)=>[i,r.value])}],{xLabel:i=>history[Math.round(i)]?.timestamp.replace('T',' ').slice(5,16)||'',tooltip:i=>{const r=history[Math.round(i)];return r?r.timestamp.replace('T',' ')+'\nMVIX '+fmt(r.value,2):'';}});

 $('tq-quality').textContent=(d.research_reason||'MVIX 漂移基于对应现货指数；期货换月不进入研究序列。')+' '+(m?'最近 MVIX 快照 '+m.timestamp+' · '+(m.quality==='valid'?'有效，利率为配置假设':m.details.reason||'报价不足'):'暂无 MVIX 快照；不使用成交价代替中间价。')+' '+d.cache.history_note;
 if(d.research){researchData=d.research;renderResearch();$('metric-vix').textContent=fmt(valid?.value,2);$('mvix-summary').textContent=mvixCaption+(m?.quality!=='valid'?' · 最近采样无效，显示历史值':'');$('export').disabled=false;return;}
 researchData=null;$('cache-status').hidden=true;const ib=d.index_bars,last=ib.at(-1);
 $('metric-price').textContent=fmt(last?.close,1);$('symbol-mini').textContent=d.product+' 指数';$('metric-date').textContent=last?.timestamp.replace('T',' ')||'等待行情';$('metric-vix').textContent=fmt(valid?.value,2);$('mvix-summary').textContent=mvixCaption+(valid&&m?.quality!=='valid'?' · 最近采样无效，显示历史值':'');$('metric-corr').textContent='—';$('level-corr').textContent='等待同期 MVIX 历史';$('metric-drift').textContent='—';$('metric-state').textContent='样本积累中';
 chart('market-chart',[{name:'指数',data:ib.map((r,i)=>[i,r.close])},{name:'MVIX',right:true,points:true,data:ib.map((r,i)=>[i,r.vix])}],{dual:true,xLabel:i=>ib[Math.round(i)]?.timestamp.slice(11,16)||''});
 ['phase-chart','residual-chart','correlation-chart','visits-chart','hv-chart','sensitivity-chart'].forEach(id=>{$(id).replaceChildren();$(id).textContent='MVIX 有效样本不足，暂不生成漂移信号';});
 $('visits-body').replaceChildren();$('visit-count').textContent='0 次回访';$('data-range').textContent=ib.length+' 根指数行情 / '+d.period+' 分钟';
}
