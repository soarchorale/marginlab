'use strict';
(()=>{
const $=id=>document.getElementById(id),NS='http://www.w3.org/2000/svg';let bars=[],playing=false,loading=false,version=0;
function add(svg,tag,a={},t){const e=document.createElementNS(NS,tag);Object.entries(a).forEach(([k,v])=>e.setAttribute(k,v));if(t!=null)e.textContent=t;svg.append(e);return e;}
function canvas(id){const box=$(id);box.replaceChildren();const w=Math.max(300,box.clientWidth),h=box.clientHeight;return {svg:add(box,'svg',{viewBox:`0 0 ${w} ${h}`,role:'img','aria-label':id}),w,h};}
function draw(){
 const idx=Number($('chip-position').value);const n=Math.max(20,Math.min(1500,Number($('cfgWindow').value)||240)),bins=Math.max(10,Math.min(100,Number($('cfgBins').value)||40));
 const selected=bars.slice(Math.max(0,idx-n+1),idx+1);if(!selected.length)return;
 $('chip-time').textContent=selected.at(-1).timestamp.replace('T',' ');
 let lo=Math.min(...selected.map(r=>r.close)),hi=Math.max(...selected.map(r=>r.close));if(lo===hi){lo-=1;hi+=1;}
 const {svg,w,h}=canvas('chip-price'),Y=p=>20+(hi-p)/(hi-lo)*(h-50),X=i=>52+i/Math.max(1,selected.length-1)*(w-70);
 for(let j=0;j<5;j++){const p=lo+(hi-lo)*j/4;add(svg,'text',{x:4,y:Y(p)+4},p.toFixed(1));add(svg,'line',{x1:50,x2:w-15,y1:Y(p),y2:Y(p),stroke:'#edf0f2'});}
 add(svg,'path',{d:selected.map((r,i)=>`${i?'L':'M'}${X(i)},${Y(r.close)}`).join(' '),stroke:'#198c80',fill:'none','stroke-width':1.8});
 const maN=Math.max(2,Math.min(200,Number($('chip-ma').value)||20));let ma='';selected.forEach((r,i)=>{if(i>=maN-1){const avg=selected.slice(i-maN+1,i+1).reduce((s,x)=>s+x.close,0)/maN;ma+=`${ma?'L':'M'}${X(i)},${Y(avg)} `;}});add(svg,'path',{d:ma,stroke:'#d48e50',fill:'none','stroke-width':1.4});
 const grid=Array.from({length:bins},()=>Array(5).fill(0));selected.forEach((r,i)=>{const b=Math.min(bins-1,Math.floor((r.close-lo)/(hi-lo)*bins)),layer=Math.min(4,Math.floor(i/selected.length*5));grid[b][layer]+=r.volume;});
 const c=canvas('chip-profile'),max=Math.max(1,...grid.map(a=>a.reduce((s,v)=>s+v,0))),bh=(c.h-50)/bins;
 grid.forEach((layers,i)=>{let offset=0;layers.forEach((v,j)=>{const rect=add(c.svg,'rect',{x:55+offset/max*(c.w-80),y:20+(bins-i-1)*bh,width:v/max*(c.w-80),height:Math.max(.5,bh-1),fill:`hsl(169 37% ${78-j*12}%)`});add(rect,'title',{},`价格 ${(lo+(i+.5)*(hi-lo)/bins).toFixed(2)} · 成交量 ${v}`);offset+=v;});});
 for(let j=0;j<5;j++)add(c.svg,'text',{x:3,y:24+j*(c.h-50)/4},(hi-(hi-lo)*j/4).toFixed(1));
 add(c.svg,'text',{x:55,y:c.h-8},'0');add(c.svg,'text',{x:c.w-20,y:c.h-8,'text-anchor':'end'},Math.round(max));
 $('chip-note').textContent='窗口成交量 '+selected.reduce((s,r)=>s+r.volume,0).toLocaleString()+'。分钟成交量按收盘价归集，仅供观察成交集中区域；不代表真实逐笔筹码或持仓成本。';
}
async function load(e){e?.preventDefault();if(loading)return;loading=true;$('chip-refresh').disabled=true;const id=++version;
 try{const res=await window.marginFetch('/api/chips',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:$('chip-symbol').value})});const d=await res.json();if(!res.ok)throw Error(d.error);if(id!==version)return;const oldEnd=Number($('chip-position').value)>=bars.length-1;bars=d.bars;$('chip-position').max=Math.max(0,bars.length-1);if(oldEnd)$('chip-position').value=Math.max(0,bars.length-1);$('chip-title').textContent=d.status.contract||'正在解析主力合约';$('chip-status').textContent=(d.collector.error||'TqSdk · '+d.collector.state)+' · 最新行情 '+(d.status.quote_time||'等待首次订阅')+'。'+d.note;if(bars.length)draw();else{$('chip-price').textContent='等待分钟行情…';$('chip-profile').textContent='暂无分布';}}
 catch(error){$('chip-status').textContent=error.message;}
 finally{loading=false;$('chip-refresh').disabled=false;}
}
$('chip-form').addEventListener('submit',load);$('chip-position').addEventListener('input',draw);['cfgBins','cfgWindow','chip-ma'].forEach(id=>$(id).addEventListener('change',draw));
$('chip-symbol').addEventListener('change',()=>{bars=[];playing=false;$('chip-play').textContent='播放';load();});
$('chip-play').addEventListener('click',()=>{playing=!playing;$('chip-play').textContent=playing?'暂停':'播放';if(playing&&Number($('chip-position').value)>=bars.length-1)$('chip-position').value=0;});
setInterval(()=>{if(playing&&bars.length&&!document.hidden){$('chip-position').value=Math.min(bars.length-1,Number($('chip-position').value)+1);draw();if(Number($('chip-position').value)>=bars.length-1){playing=false;$('chip-play').textContent='播放';}}},150);
setInterval(()=>{if(!document.hidden&&!playing)load();},15000);window.addEventListener('resize',draw);load();
})();
