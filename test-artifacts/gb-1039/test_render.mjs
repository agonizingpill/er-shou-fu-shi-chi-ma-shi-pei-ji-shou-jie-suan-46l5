// 用真实页面模块渲染第 03 页，检查整批排班面板的结构与转义。
// 运行：cd /tmp/uitest && node /workspace/test-artifacts/gb-1039/test_render.mjs
import {JSDOM} from 'jsdom';
import fs from 'node:fs';

const dom=new JSDOM('<!doctype html><div id="app"></div>',{url:'http://localhost/'});
for(const k of ['window','document','localStorage','FormData'])globalThis[k]=dom.window[k];
globalThis.fetch=async()=>({ok:true,json:async()=>({})});
localStorage.setItem('resale-user',JSON.stringify({token:'t',email:'supervisor@hesheng.local',role:'supervisor'}));

const root='/workspace/frontend/src';
const mods={};
function load(rel){
  if(rel in mods)return mods[rel];
  let code=fs.readFileSync(root+'/'+rel,'utf8');
  const dir=rel.split('/').slice(0,-1).join('/');
  const deps=[];
  code=code.replace(/import\s*\{([^}]+)\}\s*from'([^']+)'/g,(m,names,spec)=>{
    const parts=(dir?dir+'/'+spec:spec).split('/');
    const target=parts.reduce((a,p)=>p==='..'?a.slice(0,-1):p==='.'?a:a.concat(p),[]).join('/');
    deps.push([names,target]);return '';
  });
  const names=[...code.matchAll(/export (?:async )?(?:function|const|let) (\w+)/g)].map(m=>m[1]);
  const body=code.replace(/export\s+(?=(?:async )?(?:function|const|let)\s)/g,'');
  const pre=deps.map(([n,d])=>`const {${n}}=__cache[${JSON.stringify(d)}];`).join('\n');
  const fn=new Function('__cache',pre+body+`\nreturn {${names.join(',')}};`);
  for(const [,d] of deps)load(d);
  mods[rel]=fn(mods);
  return mods[rel];
}
const fittings=load('pages/fittings.js');

const data={
 clock:[{id:1,now:'2026-11-18T09:00:00'}],
 garments:[{id:5,code:'G035',title:'燕麦色羊毛开衫',state:'listed',price_cents:26000},{id:6,code:'G036',title:'靛蓝弹力高领衫',state:'listed',price_cents:26000}],
 profiles:[{id:1,code:'P031',chest:88,shoulder:37},{id:2,code:'P032',chest:92,shoulder:38},{id:3,code:'P033',chest:102,shoulder:43}],
 fittings:[],
 batches:[{id:1,created_by:'supervisor@hesheng.local',created_at:'2026-11-18T09:00:00',note:'周末候补试衣预排',state:'draft',plan_version:2,confirmed_at:''}],
 batch_windows:[{id:1,batch_id:1,starts:'2026-11-21T10:00:00',ends:'2026-11-21T11:00:00'},{id:2,batch_id:1,starts:'2026-11-21T13:00:00',ends:'2026-11-21T14:00:00'}],
 batch_buyers:[{id:1,batch_id:1,profile_id:1,wait_since:'2026-11-10T09:00:00',windows:[0,1]},{id:2,batch_id:1,profile_id:2,wait_since:'2026-11-12T09:00:00',windows:[0,1]},{id:3,batch_id:1,profile_id:3,wait_since:'2026-11-01T09:00:00',windows:[0,1]}],
 batch_items:[
  {id:1,batch_id:1,version:2,buyer_id:1,garment_id:5,window_id:1,slot:1,starts:'2026-11-21T10:00:00',ends:'2026-11-21T11:00:00',fit_label:'合身',fit_probability:.94,state:'scheduled',reason:''},
  {id:2,batch_id:1,version:2,buyer_id:2,garment_id:6,window_id:2,slot:2,starts:'2026-11-21T13:00:00',ends:'2026-11-21T14:00:00',fit_label:'合身',fit_probability:.98,state:'scheduled',reason:''},
  {id:3,batch_id:1,version:2,buyer_id:3,garment_id:null,window_id:null,slot:null,starts:null,ends:null,fit_label:'',fit_probability:0,state:'unscheduled',reason:'尺码模型没有给出合身衣物（最接近：偏紧）'},
  {id:4,batch_id:1,version:1,buyer_id:1,garment_id:5,window_id:1,slot:1,starts:'2026-11-21T10:00:00',ends:'2026-11-21T11:00:00',fit_label:'合身',fit_probability:.94,state:'scheduled',reason:''},
 ],
};
const html=fittings.render(data);
// 只读视角：清空模块缓存、换 viewer 身份重载，不应出现写按钮或建批表单
for(const k of Object.keys(mods))delete mods[k];
localStorage.setItem('resale-user',JSON.stringify({token:'t',email:'viewer@hesheng.local',role:'viewer'}));
const viewerHtml=load('pages/fittings.js').render(data);
fs.writeFileSync('/workspace/test-artifacts/gb-1039/render_fittings.html',`<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="../../frontend/style.css">${html}`);
const checks={
 '批次面板存在':html.includes('batch-card'),
 '两个试衣位泳道':(html.match(/class="batch-lane"/g)||[]).length===2,
 '第 2 版':html.includes('第 2 版'),
 '未排原因显示':html.includes('尺码模型没有给出合身衣物'),
 '排定衣物与试衣位':html.includes('G035')&&html.includes('试衣位 1'),
 '确认按钮':html.includes('/batches/1/confirm'),
 '重排按钮':html.includes('/batches/1/replan'),
 '等待天数':html.includes('等 8 天')||html.includes('等 6 天'),
 '版本隔离（只显示当前版本2席）':(html.match(/class="batch-seat/g)||[]).length===2,
 '候补名单3行':(html.match(/<tr>/g)||[]).length>=3,
 '无未转义标签':!html.includes('<script>'),
 '只读不显示确认按钮':!viewerHtml.includes('/batches/1/confirm'),
 '只读不显示建批表单':!viewerHtml.includes('batch-create'),
 '只读仍可查看排定安排':viewerHtml.includes('G035')&&viewerHtml.includes('未排上'),
};
let bad=0;
for(const [k,v] of Object.entries(checks)){console.log((v?'PASS':'FAIL')+' '+k);if(!v)bad++}
process.exit(bad?1:0);
