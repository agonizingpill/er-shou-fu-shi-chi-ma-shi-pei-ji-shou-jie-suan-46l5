import{heading,form,input,select,badge}from'../components/tailor.js';import{opts,esc}from'../utils/format.js';import{user}from'../utils/api.js';

let selected=null;
export function selectBatch(id){selected=Number(id)}

const shortTime=v=>v?String(v).replace('T',' ').slice(5,16):'—';
const clockOnly=v=>v?String(v).slice(11,16):'—';
const stateText=s=>({scheduled:'已排定',confirmed:'已确认',unscheduled:'未排上'})[s]||s;
const waitText=(now,since)=>{if(!since)return '—';let d=Math.max(0,Math.floor((new Date(now)-new Date(since))/86400000));return d>=1?`等 ${d} 天`:`等 ${Math.max(1,Math.floor((new Date(now)-new Date(since))/60000))} 分钟`};

function board(items,ctx){
 const lane={1:[],2:[]};
 for(const x of items){if(!lane[x.slot])lane[x.slot]=[];lane[x.slot].push(x)}
 return `<div class="batch-board"><div class="rooms"><span>试衣位 01</span><span>试衣位 02</span></div><div class="batch-lanes">${[1,2].map(s=>`<div class="batch-lane"><h3>试衣位 0${s}<small>${lane[s].length} 位</small></h3>${lane[s].sort((a,b)=>String(a.starts).localeCompare(String(b.starts))).map(it=>{const g=ctx.garment(it.garment_id);return `<div class="batch-seat ${esc(it.state)}"><small>${shortTime(it.starts)} – ${shortTime(it.ends).slice(6)}</small><b>${esc(g?.code||'—')}</b><span>${esc(ctx.profile(ctx.profileId(it.buyer_id)))}</span><em class="fit-tag ${esc(it.fit_label)}">${esc(it.fit_label)} ${(it.fit_probability*100).toFixed(0)}%</em></div>`}).join('')||'<p class="empty">空位</p>'}</div>`).join('')}</div></div>`;
}

function batchCard(d,b){
 const ctx={
  item:x=>d.batch_items.find(i=>i.batch_id===b.id&&i.version===b.plan_version&&i.buyer_id===x.id)||{},
  garment:id=>d.garments.find(g=>g.id===id),
  profile:id=>d.profiles.find(p=>p.id===id)?.code||'—',
  profileId:id=>d.batch_buyers.find(x=>x.id===id)?.profile_id,
 };
 const buyers=d.batch_buyers.filter(x=>x.batch_id===b.id);
 const wins=d.batch_windows.filter(w=>w.batch_id===b.id);
 let items=buyers.map(ctx.item);
 if(!items.length)return `<p class="empty">该批次暂无预排版本。</p>`;
 const on=items.filter(x=>x.state==='scheduled'||x.state==='confirmed');
 const editable=b.state==='draft'&&user.role!=='viewer';
 const stateLabel=b.state==='confirmed'?'已确认落单':'预排中';
 const stateClass=b.state==='confirmed'?'settled':'ready';
 return `<div class="batch-card" data-batch="${b.id}"><div class="batch-meta"><b>第 ${b.id} 批 · ${esc(b.note||'周末预排')}</b><span class="plan-version">第 ${b.plan_version} 版</span><span class="batch-state ${stateClass}">${stateLabel}</span><span>${buyers.length} 位候补 · 排上 ${on.length} 位</span><small>${esc(b.created_by)} · ${esc(shortTime(b.created_at))}${b.confirmed_at?' · 确认于 '+esc(shortTime(b.confirmed_at)):''}</small></div>
 ${board(on,ctx)}
 <table class="batch-table"><thead><tr><th>候补买家</th><th>等待</th><th>可到店时段</th><th>排定安排</th><th>状态 / 原因</th></tr></thead><tbody>${buyers.map(x=>{const it=ctx.item(x);const g=ctx.garment(it.garment_id);return `<tr><td>${esc(ctx.profile(x.profile_id))}</td><td>${esc(waitText(d.clock[0].now,x.wait_since))}</td><td>${(x.windows||[]).map(i=>wins[i]?shortTime(wins[i].starts).slice(0,11):'').filter(Boolean).join(' / ')}</td><td>${g?`${esc(g.code)} · 试衣位 ${it.slot}<br><small>${esc(shortTime(it.starts))}</small>`:'<i class="muted">未排上</i>'}</td><td>${g?`<span class="batch-status ${esc(it.state)}">${esc(stateText(it.state))}</span> `:''}${it.reason?`<small class="${g?'':'reason'}">${esc(it.reason)}</small>`:''}</td></tr>`}).join('')}</tbody></table>
 ${editable?`<div class="batch-actions"><button data-action="/batches/${b.id}/replan">按当前衣物与空位重排</button><button data-action="/batches/${b.id}/confirm" class="primary">确认整批落单留衣</button><p>预排不占衣物与试衣位；确认时才整批留衣，并与单笔预约并发核对。</p></div>`:b.state==='confirmed'?`<p class="batch-done">本批已确认落单，衣物与试衣位已按上方安排占用；安排同时出现在上方单笔预约中。</p>`:''}
 </div>`;
}

function batchForm(d){
 const defs=[['2026-11-21T10:00:00','2026-11-21T11:00:00'],['2026-11-21T13:00:00','2026-11-21T14:00:00'],['2026-11-21T15:00:00','2026-11-21T16:00:00'],['2026-11-22T11:00:00','2026-11-22T12:00:00'],['2026-11-22T14:00:00','2026-11-22T15:00:00'],['','']];
 const windowRows=defs.map((v,i)=>`<div class="window-row"><small>${i+1}</small><input name="w_starts_${i+1}" value="${v[0]}" placeholder="开始"><input name="w_ends_${i+1}" value="${v[1]}" placeholder="结束"></div>`).join('');
 const buyerRows=Array.from({length:6},(_,i)=>i+1).map(i=>`<div class="buyer-row"><select name="b_profile_${i}"><option value="">— 候补买家 —</option>${d.profiles.map(p=>`<option value="${p.id}">${esc(p.code)} · 胸${p.chest} 肩${p.shoulder}</option>`).join('')}</select><input name="b_since_${i}" value="${i<=2?'2026-11-1'+(i+5)+'T09:00:00':''}" placeholder="候补起始 ISO"><div class="window-picks">${[1,2,3,4,5,6].map(n=>`<label title="时段 ${n}"><input type="checkbox" name="b_windows_${i}" value="${n-1}" ${i<=2&&n<=3?'checked':''}>${n}</label>`).join('')}</div></div>`).join('');
 return `<form id="batch-create" data-endpoint="/batches" data-payload="batch"><h3>建立周末预排</h3><label>批次备注<input name="note" value="周末候补试衣预排" maxlength="300"></label><fieldset><legend>可到店时段（留空忽略）</legend>${windowRows}</fieldset><fieldset><legend>候补买家（未选买家忽略）</legend><p class="hint">右侧数字勾选该买家可到的时段序号。</p>${buyerRows}</fieldset><button>生成预排，查看安排</button></form>`;
}

export function render(d){
 const batches=(d.batches||[]).slice().sort((a,b)=>b.id-a.id);
 if(selected==null||!batches.some(b=>b.id===selected))selected=batches[0]?.id;
 return heading('03','留衣只留一位，试衣各有时间。','单笔预约一件衣物只给一位买家，两个试衣位按时段互斥；周末排不过来时，按候补整批预排——先让排上的人尽可能多，再优先等得久的。')+
 `<div class="atelier"><section><div class="rooms"><span>试衣位 01</span><span>试衣位 02</span></div>${d.fittings.map(f=>`<article class="fitting-ticket"><div><small>FITTING #${f.id} / 试衣位 ${f.slot}</small><h2>${d.garments.find(g=>g.id===f.garment_id)?.code}</h2><p>${d.profiles.find(x=>x.id===f.profile_id)?.code} · ${esc(f.starts)}<br>至 ${esc(f.ends)}</p></div><div>${badge(f.state)}${f.state==='reserved'?`<button data-action="/fittings/${f.id}/cancel">取消预约，释放留衣</button>`:''}</div></article>`).join('')}
 <h2 class="batch-title">周末候补整批排班</h2><div class="batch-tabs">${batches.map(b=>`<button data-batch-select="${b.id}" class="${b.id===selected?'selected':''}">第 ${b.id} 批 · ${b.state==='confirmed'?'已确认':'预排中'} · ${b.plan_version} 版</button>`).join('')}</div>${batches.length?batchCard(d,batches.find(b=>b.id===selected)):'<p class="empty">还没有预排批次；顾问可在右侧建立。预排只读衣物与试衣位，不落单。</p>'}</section><aside>${form('fitting-create','预约并排他留衣','/fittings',select('garment_id','在架衣物',opts(d.garments.filter(x=>x.state==='listed'),x=>x.code+' / '+x.title))+select('profile_id','匿名买家',opts(d.profiles,x=>x.code))+input('slot','试衣位1或2',1,'number')+input('starts','开始ISO时间','2026-12-02T15:00:00')+input('ends','结束ISO时间','2026-12-02T16:00:00'))}${user.role==='viewer'?'':batchForm(d)}</aside></div>`;
}
