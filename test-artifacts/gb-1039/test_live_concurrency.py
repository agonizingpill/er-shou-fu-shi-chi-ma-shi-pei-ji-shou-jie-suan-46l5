"""对真实 uvicorn + PostgreSQL 做并发验收：
1) 批量确认与单笔预约同时提交，不出现同衣/同试衣位重复占用；
2) 两个确认请求并发，不重复留衣；
3) 确认重试幂等。
运行前需 uvicorn 已在 BASE 启动。
"""
import json, os, sys, threading, urllib.request, urllib.error
BASE=os.environ.get('BASE','http://127.0.0.1:18000')
sys.path.insert(0,os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','backend')))

def call(path,data=None,token=None,method=None):
    req=urllib.request.Request(BASE+path,data=None if data is None else json.dumps(data).encode(),
        method=method or ('POST' if data is not None else 'GET'),
        headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token else {})})
    try:
        with urllib.request.urlopen(req) as r:return r.status,json.loads(r.read())
    except urllib.error.HTTPError as e:return e.code,json.loads(e.read())

def login(who='supervisor'):
    s,b=call('/auth/login',{'email':who+'@hesheng.local','password':'Fit2026!'})
    assert s==200,(s,b);return b['token']

def reset():
    from src.database import Base,engine,Session
    from src.models import User,Shop,Consignor,Agreement,Garment,Measurement,FitProfile
    from src.security import password_hash
    Base.metadata.drop_all(engine);Base.metadata.create_all(engine)
    with Session() as s:
        for r in ('operator','supervisor','viewer'):
            s.add(User(email=r+'@hesheng.local',password=password_hash('Fit2026!'),role=r))
        s.add(Shop(id=1,now='2026-11-18T09:00:00'))
        s.add(Consignor(id=1,name='林禾',contact='x'));s.flush()
        s.add(Agreement(id=1,code='A1',consignor_id=1,floor_cents=18000,commission_bp=2000,ends='2026-11-30T18:00:00'));s.flush()
        for i,(code,ch,sh) in enumerate([('G1',96,39),('G2',96,39),('G3',96,39)],1):
            s.add(Garment(id=i,code=code,title='衣'+code,agreement_id=1,condition='八成新，无破损',price_cents=22000,state='listed'));s.flush()
            s.add(Measurement(garment_id=i,chest=ch,shoulder=sh,length=58,elasticity=.2,cut=1))
        for i,(code,ch,sh) in enumerate([('P1',88,37),('P2',88,37),('P3',88,37),('P4',85,36)],1):
            s.add(FitProfile(id=i,code=code,chest=ch,shoulder=sh))
        s.commit()

def payload():
    return {'note':'并发验收','windows':[
        {'starts':'2026-11-21T10:00:00','ends':'2026-11-21T11:00:00'},
        {'starts':'2026-11-21T11:00:00','ends':'2026-11-21T12:00:00'}],
        'buyers':[{'profile_id':i,'wait_since':'2026-11-10T09:00:00','windows':[0,1]} for i in (1,2,3)]}

def post(path,token=None):
    return call(path,{},token)

def run_pair(a,b):
    out={}
    def go(k,fn):out[k]=fn()
    ta=threading.Thread(target=go,args=('a',a));tb=threading.Thread(target=go,args=('b',b))
    ta.start();tb.start();ta.join();tb.join();return out

def main():
    results=[]
    reset();token=login()
    s,v=call('/batches',payload(),token);assert s==200,(s,v)
    # 1) 单笔预约与批量确认同时提交
    out=run_pair(lambda:call('/fittings',{'garment_id':1,'profile_id':4,'slot':1,'starts':'2026-11-21T10:00:00','ends':'2026-11-21T11:00:00'},token),
                 lambda:post('/batches/1/confirm',token))
    reg=call('/register',None,token)[1]
    reserved=[f for f in reg['fittings'] if f['state']=='reserved']
    # 不得同衣两留；同试衣位的时间段不得重叠
    dup_garment=len({f['garment_id'] for f in reserved})!=len(reserved)
    overlap=any(f1['slot']==f2['slot'] and f1['starts']<f2['ends'] and f2['starts']<f1['ends']
                for i,f1 in enumerate(reserved) for f2 in reserved[i+1:])
    results.append(('并发：单笔+整批不重复占用',(out['a'][0],out['b'][0]),not dup_garment and not overlap))
    # 2) 两个确认并发
    reset();token=login();call('/batches',payload(),token)
    out=run_pair(lambda:post('/batches/1/confirm',token),lambda:post('/batches/1/confirm',token))
    reg=call('/register',None,token)[1]
    assigned=[x for x in reg['batch_items'] if x['version']==1 and x['state']=='confirmed']
    ok=len(reg['fittings'])==len(assigned) and len(reg['fittings'])==len({f['garment_id'] for f in reg['fittings']})
    results.append(('并发：两次确认不重复留衣',(out['a'][0],out['b'][0]),ok))
    # 3) 顺序重试幂等
    s1,_=post('/batches/1/confirm',token);s2,_=post('/batches/1/confirm',token)
    reg=call('/register',None,token)[1]
    results.append(('确认重试幂等',(s1,s2),s1==200 and s2==200 and len(reg['fittings'])==len(assigned)))
    # 4) 冲突后 409 → 重排 → 再确认
    reset();token=login();call('/batches',payload(),token)
    assert call('/fittings',{'garment_id':1,'profile_id':4,'slot':2,'starts':'2026-11-21T10:00:00','ends':'2026-11-21T11:00:00'},token)[0]==200
    s,v=post('/batches/1/confirm',token)
    v2=call('/batches/1',None,token)[1]
    s3,v3=post('/batches/1/confirm',token)
    reg=call('/register',None,token)[1]
    confirmed=[x for x in reg['batch_items'] if x['version']==2 and x['state']=='confirmed']
    # 复核：重排后确认只按第 2 版落单，无重复衣物/试衣位，原并发单笔仍在
    keys=[(f['garment_id'],f['slot'],f['starts']) for f in reg['fittings'] if f['state']=='reserved']
    ok=v2['batch']['state']=='draft' and s==409 and s3==200 and len(reg['fittings'])==1+len(confirmed) \
       and len({f['garment_id'] for f in reg['fittings']})==len(reg['fittings']) and len(keys)==len(set(keys))
    results.append(('冲突 409 且整批未落单→重排→再确认',(s,v2['batch']['plan_version'],s3),ok))
    bad=0
    for r in results:
        ok=bool(r[-1]);bad+=not ok;print(('PASS' if ok else 'FAIL'),*r[:-1])
    return 1 if bad else 0

if __name__=='__main__':
    sys.exit(main())
