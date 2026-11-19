"""周末候补批量排班：尺码模型挑合身衣物，再按两试衣位做整批排定。

预排只读衣物与试衣位，不产生留衣；确认时才在 Shop 父锁内整批落单。
确认若与并发单笔预约相撞，整批回滚并基于剩余衣物/试衣位重排为新版本。
"""
import numpy as np
from scipy.optimize import milp, LinearConstraint, Bounds
from scipy.sparse import lil_matrix
from .models import *
from .common import *
from .fit_model import predict
from . import fitting_service

BIG=10**12
MAX_DURATION=7200


class Replanned(Exception):
    """确认时发现并发冲突：整批未落单，已生成重排版本，需重新确认。"""

    def __init__(self,message):
        super().__init__(message);self.message=message


def _overlap(a,b,c,d):return a<d and b>c
def _wait_minutes(now,since):return max(0,int((now-time(since)).total_seconds()//60))


def items_of(s,b,version=None):
    v=b.plan_version if version is None else version
    return [x for x in rows(s,BatchItem) if x.batch_id==b.id and x.version==v]


def _windows(s,b):return [w for w in rows(s,BatchWindow) if w.batch_id==b.id]
def _buyers(s,b):return [x for x in rows(s,BatchBuyer) if x.batch_id==b.id]


def _fits(buyers,garments,profiles,measurements):
    """对每位候补买家与每件已实测衣物调用真实尺码模型。"""
    cache={}
    for buyer in buyers:
        profile=profiles.get(buyer.profile_id)
        if not profile:continue
        for g in garments:
            m=measurements.get(g.id)
            if not m:continue
            try:r=predict(dump(m),dump(profile))
            except Exception:continue
            cache[(buyer.profile_id,g.id)]={'label':r['label'],'probability':float(r['probabilities'][r['label']]),'probabilities':r['probabilities']}
    return cache


def _slot_conflicts(windows):
    """同一试衣位上时间重叠的时段两两互斥（只合并直接重叠，不做传递闭包）。"""
    return [(i,j) for i in range(len(windows)) for j in range(i+1,len(windows))
            if _overlap(time(windows[i].starts),time(windows[i].ends),time(windows[j].starts),time(windows[j].ends))]


def _options(s,b,now):
    """构造全部可行的（买家, 合身衣物, 可到店时段, 试衣位）候选，并诊断未排原因。"""
    windows=_windows(s,b);buyers=_buyers(s,b)
    profiles={p.id:p for p in rows(s,FitProfile)}
    agreements={a.id:a for a in rows(s,Agreement)}
    measurements={m.garment_id:m for m in rows(s,Measurement)}
    all_garments=rows(s,Garment)
    measured={g.id:g for g in all_garments if g.id in measurements}
    existing=[f for f in rows(s,Fitting) if f.state=='reserved']
    fits=_fits(buyers,list(measured.values()),profiles,measurements)
    windex={w.id:i for i,w in enumerate(windows)}
    options=[];diagnostics={}
    for buyer in buyers:
        my_fits={g.id:fits[(buyer.profile_id,g.id)] for g in measured.values() if (buyer.profile_id,g.id) in fits}
        fit_any=[(gid,my_fits[gid]) for gid in measured if my_fits.get(gid,{}).get('label')=='合身']
        listed_fit=[(gid,f) for gid,f in fit_any if measured[gid].state=='listed']
        chosen=[w for i,w in enumerate(windows) if i in (buyer.windows or [])]
        pair_ok=[]
        for w in chosen:
            ws,we=time(w.starts),time(w.ends)
            if ws<now:continue
            for gid,f in listed_fit:
                g=measured[gid];a=agreements.get(g.agreement_id)
                if not a or we>time(a.ends):continue
                if any(x.garment_id==gid and _overlap(ws,we,time(x.starts),time(x.ends)) for x in existing):continue
                pair_ok.append((g,w,f))
        for g,w,f in pair_ok:
            ws,we=time(w.starts),time(w.ends)
            for slot in (1,2):
                if any(x.slot==slot and _overlap(ws,we,time(x.starts),time(x.ends)) for x in existing):continue
                options.append({'buyer':buyer,'garment':g,'window':w,'slot':slot,
                                'wait':_wait_minutes(now,buyer.wait_since),
                                'probability':f['probability'],'label':f['label'],
                                'window_index':windex[w.id]})
        if buyer.id in {o['buyer'].id for o in options}:continue
        if not chosen:
            diagnostics[buyer.id]='未选择可到店时段或所选时段已过'
        elif not fit_any:
            best=max(my_fits.values(),key=lambda f:f['probability'],default=None)
            if best and best['label']=='偏紧':diagnostics[buyer.id]='尺码模型判定在库衣物均偏紧，没有合身款可留'
            elif best and best['label']=='偏松':diagnostics[buyer.id]='尺码模型判定在库衣物均偏松，没有合身款可留'
            elif best:diagnostics[buyer.id]=f"尺码模型没有给出确定合身款（最接近为「{best['label']}」）"
            else:diagnostics[buyer.id]='匿名体型与在库实测衣物不足，无法计算适配'
        elif not listed_fit:
            diagnostics[buyer.id]='合身衣物当前均不在架（已留衣、已售或待复检）'
        elif not pair_ok:
            diagnostics[buyer.id]='可到店时段超出合身衣物的有效寄售期，或衣物在该时段已被留衣'
        else:
            diagnostics[buyer.id]='所选时段的两个试衣位都已被单笔预约占用'
    return windows,buyers,options,diagnostics


def _score(choice,waits):
    return (len(choice),sum(waits[o['buyer'].id] for o in choice.values()),
            sum(int(round(o['probability']*999)) for o in choice.values()))


def _greedy(options,buyers,windows,conflicts,now):
    """保底可行解：等待越久越先挑，先到先得地占衣物与试衣位。"""
    conflict=set(conflicts)
    used_g=set();used_slot=[]
    choice={}
    def slot_free(windex_,slot):
        for i,s in used_slot:
            if s!=slot:continue
            if i==windex_ or (min(windex_,i),max(windex_,i)) in conflict:return False
        return True
    for buyer in sorted(buyers,key=lambda b:(-_wait_minutes(now,b.wait_since),b.id)):
        opts=sorted((o for o in options if o['buyer'].id==buyer.id),
                    key=lambda o:(time(o['window'].starts),o['slot'],-o['probability'],o['garment'].id))
        for o in opts:
            if o['garment'].id in used_g or not slot_free(o['window_index'],o['slot']):continue
            choice[buyer.id]=o;used_g.add(o['garment'].id);used_slot.append((o['window_index'],o['slot']));break
    return choice


def _optimize(options,buyers,windows,conflicts,now):
    """整数规划：先最大化排上人数，再最大化累计等待时长，最后偏向模型概率高的搭配。"""
    waits={b.id:_wait_minutes(now,b.wait_since) for b in buyers}
    choice=_greedy(options,buyers,windows,conflicts,now)
    if options:
        n=len(options)
        weights=np.array([BIG+o['wait']*1000+int(round(o['probability']*999)) for o in options],dtype=float)
        groups={}
        for i,o in enumerate(options):
            groups.setdefault(('b',o['buyer'].id),[]).append(i)
            groups.setdefault(('g',o['garment'].id),[]).append(i)
            groups.setdefault(('s',o['slot'],o['window_index']),[]).append(i)
        rows_=[idx for idx in groups.values()]
        # 同一试衣位、窗口两两重叠：最多只能占其一；逐条单独成约束行
        conflict_groups=[(slot,i,j) for i,j in conflicts for slot in (1,2)]
        A=lil_matrix((len(rows_)+len(conflict_groups),n))
        for r,idx in enumerate(rows_):A[r,idx]=1
        for k,(slot,i,j) in enumerate(conflict_groups):
            r=len(rows_)+k
            for idx,o in enumerate(options):
                if o['slot']==slot and o['window_index'] in (i,j):A[r,idx]=1
        try:
            res=milp(c=-weights,constraints=LinearConstraint(A.tocsr(),-np.inf,1),
                     integrality=np.ones(n),bounds=Bounds(0,1),options={'time_limit':5})
            if res.x is not None:
                exact={options[i]['buyer'].id:options[i] for i in range(n) if res.x[i]>0.5}
                if _score(exact,waits)>_score(choice,waits):choice=exact
        except Exception:
            pass
    return choice


def _plan(s,b,now):
    windows,buyers,options,diagnostics=_options(s,b,now)
    conflicts=_slot_conflicts(windows)
    choice=_optimize(options,buyers,windows,conflicts,now)
    for buyer in buyers:
        diagnostics.setdefault(buyer.id,'本批试衣位与合身衣物有限，已优先安排等待更久的买家')
    return windows,buyers,choice,diagnostics


def _write_version(s,b,version,now,previous,texts):
    windows,buyers,choice,diagnostics=_plan(s,b,now)
    wmap={w.id:w for w in windows}
    prev={x.buyer_id:x for x in previous}
    for buyer in buyers:
        o=choice.get(buyer.id);old=prev.get(buyer.id)
        if o:
            wall=wmap[o['window'].id]
            if old and old.state in ('scheduled','confirmed') and old.garment_id==o['garment'].id and old.window_id==wall.id and old.slot==o['slot']:
                reason=texts['same']
            elif old and old.state in ('scheduled','confirmed'):
                reason=texts['moved']
            elif old:
                reason=texts['added']
            else:
                reason=''
            s.add(BatchItem(batch_id=b.id,version=version,buyer_id=buyer.id,garment_id=o['garment'].id,
                            window_id=wall.id,slot=o['slot'],starts=wall.starts,ends=wall.ends,
                            fit_label=o['label'],fit_probability=round(o['probability'],4),state='scheduled',reason=reason))
        else:
            base=diagnostics.get(buyer.id,'本轮未能排上')
            if old and old.state in ('scheduled','confirmed'):
                reason=f"{texts['lost']}；{base}"
            elif old and texts.get('refresh'):
                reason=f"{base}（与上一版相同）"
            else:
                reason=base
            s.add(BatchItem(batch_id=b.id,version=version,buyer_id=buyer.id,state='unscheduled',reason=reason))
    b.plan_version=version


def create(s,d,actor):
    c=lock(s);now=time(c.now)
    if not d['windows']:fail('至少需要一个可到店时段')
    windows=[];seen=set()
    for w in d['windows']:
        st,en=time(w['starts']),time(w['ends'])
        if st>=en:fail('可到店时段的开始必须早于结束')
        if (en-st).total_seconds()>MAX_DURATION:fail('单次试衣最多两小时')
        if st<now:fail('可到店时段不能早于当前演示时间')
        if (st,en) in seen:fail('可到店时段重复')
        seen.add((st,en));windows.append((st,en))
    buyers=[];profiles=set()
    for x in d['buyers']:
        get(s,FitProfile,x['profile_id'])
        if x['profile_id'] in profiles:fail('同一匿名买家在一批中只能出现一次')
        profiles.add(x['profile_id'])
        since=time(x['wait_since'])
        if since>now:fail('候补起始时间不能晚于当前演示时间')
        idx=sorted(set(x['windows']))
        if any(not 0<=i<len(windows) for i in idx):fail('候补买家选择的可到店时段不存在')
        if not idx:fail('每位候补买家至少选择一个可到店时段')
        buyers.append({'profile_id':x['profile_id'],'wait_since':since,'windows':idx})
    if not buyers:fail('至少需要一位候补买家')
    b=FittingBatch(created_by=actor,created_at=c.now,note=d.get('note',''),state='draft',plan_version=1)
    s.add(b);s.flush()
    for st,en in windows:s.add(BatchWindow(batch_id=b.id,starts=st.isoformat(),ends=en.isoformat()))
    for x in buyers:s.add(BatchBuyer(batch_id=b.id,profile_id=x['profile_id'],wait_since=x['wait_since'].isoformat(),windows=x['windows']))
    s.flush()
    _write_version(s,b,1,now,[],{'same':'','moved':'','added':'','lost':''})
    s.flush()
    return view(s,b)


def replan(s,id):
    c=lock(s);b=get(s,FittingBatch,id)
    if b.state!='draft':fail('只有预排中的批次可以重排')
    previous=items_of(s,b)
    _write_version(s,b,b.plan_version+1,time(c.now),previous,
                   {'same':'本版安排不变','moved':'按当前剩余衣物与空位改排','added':'重排后补入','lost':'重排后未能保留原安排','refresh':True})
    s.flush()
    return view(s,b)


def confirm(s,id):
    """整批确认。冲突时不落单：先把重排结果写为 next_version 并返回，由路由提交后报 409。"""
    c=lock(s);b=get(s,FittingBatch,id);now=time(c.now)
    if b.state=='confirmed':return view(s,b),None
    if b.state!='draft':fail('批次状态不允许确认')
    items=items_of(s,b)
    scheduled=[x for x in items if x.state=='scheduled']
    if not scheduled:fail('本批没有排上的买家，请调整衣物或时段后重排')
    conflicts=[]
    seen_garment=set()
    for x in scheduled:
        g=s.get(Garment,x.garment_id);w=s.get(BatchWindow,x.window_id)
        a=s.get(Agreement,g.agreement_id) if g else None
        if not g or g.state!='listed':conflicts.append(f"{g.code if g else '衣物'} 已被他人留走或不可试穿")
        elif not w or not a or time(x.ends)>time(a.ends):conflicts.append(f'{g.code} 的寄售期已不覆盖预排时段')
        elif time(x.starts)<now:conflicts.append(f'{g.code} 的预排时段已过去')
        elif g.id in seen_garment:conflicts.append(f'{g.code} 在本批中被重复安排')
        else:
            seen_garment.add(g.id)
            for f in rows(s,Fitting):
                if f.state!='reserved' or not _overlap(time(x.starts),time(x.ends),time(f.starts),time(f.ends)):continue
                if f.slot==x.slot:conflicts.append(f'{x.starts} 试衣位 {x.slot} 已被单笔预约占用');break
                if f.garment_id==x.garment_id:conflicts.append(f'{g.code} 在同一时段已被单笔预约留衣');break
    if conflicts:
        previous=items
        _write_version(s,b,b.plan_version+1,now,previous,
                       {'same':'本版安排不变','moved':'确认遇并发冲突，已改排至剩余衣物与空位','added':'确认重排后补入','lost':'确认遇并发冲突，未能保留原安排','refresh':True})
        s.flush()
        raise Replanned(f"确认时与单笔预约并发冲突（{'；'.join(dict.fromkeys(conflicts))[:240]}），整批未落单，已按剩余衣物与空位重排为第 {b.plan_version} 版，请核对后再次确认")
    for x in scheduled:
        f=fitting_service.reserve(s,{'garment_id':x.garment_id,
                                     'profile_id':s.get(BatchBuyer,x.buyer_id).profile_id,
                                     'slot':x.slot,'starts':x.starts,'ends':x.ends})
        x.fitting_id=f.id;x.state='confirmed'
    b.state='confirmed';b.confirmed_at=c.now
    s.flush()
    return view(s,b),None


def view(s,b):
    items=[dump(x) for x in items_of(s,b)]
    return {'batch':dump(b),'windows':[dump(w) for w in _windows(s,b)],
            'buyers':[dump(x) for x in _buyers(s,b)],'items':items,
            'summary':{'scheduled':sum(1 for x in items if x['state'] in ('scheduled','confirmed')),
                       'unscheduled':sum(1 for x in items if x['state']=='unscheduled')}}
