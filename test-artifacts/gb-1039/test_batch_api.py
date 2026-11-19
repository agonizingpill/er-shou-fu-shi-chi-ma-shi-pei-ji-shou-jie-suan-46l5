"""批量排班的 API 级测试：鉴权、确认 409 重排、重试不重复留衣、只读拒绝。"""
import os, sys, tempfile, unittest
os.environ.setdefault('DATABASE_URL','sqlite:///'+tempfile.mkdtemp()+'/t.db')
os.environ.setdefault('REDIS_URL','redis://127.0.0.1:6379/0')
os.environ.setdefault('JWT_SECRET','test-secret')
sys.path.insert(0,os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','backend')))
import fakeredis
from fastapi.testclient import TestClient
import src.main as main
from src.database import Base,engine,Session
from src.models import *

W=[{'starts':'2026-11-21T10:00:00','ends':'2026-11-21T11:00:00'},
   {'starts':'2026-11-21T13:00:00','ends':'2026-11-21T14:00:00'}]

def fresh():
    Base.metadata.drop_all(engine);Base.metadata.create_all(engine)
    from src.security import password_hash
    with Session() as s:
        for r in ('operator','supervisor','viewer'):
            s.add(User(email=r+'@hesheng.local',password=password_hash('Fit2026!'),role=r))
        s.add(Shop(id=1,now='2026-11-18T09:00:00'))
        s.add(Consignor(id=1,name='林禾',contact='x'));s.flush()
        s.add(Agreement(id=1,code='A1',consignor_id=1,floor_cents=18000,commission_bp=2000,ends='2026-11-30T18:00:00'));s.flush()
        for i,(code,ch,sh) in enumerate([('G1',96,39),('G2',96,39),('G3',100,41)],1):
            s.add(Garment(id=i,code=code,title='衣'+code,agreement_id=1,condition='八成新，无破损',price_cents=22000,state='listed'));s.flush()
            s.add(Measurement(garment_id=i,chest=ch,shoulder=sh,length=58,elasticity=.2,cut=1))
        for i,(code,ch,sh) in enumerate([('P1',88,37),('P2',88,37),('P3',102,43)],1):
            s.add(FitProfile(id=i,code=code,chest=ch,shoulder=sh))
        s.commit()

def make_client():
    main.app.router.lifespan_context=type('L',(),{'__aenter__':lambda self:None,'__aexit__':lambda self,e,t,b:None})()
    return TestClient(main.app,raise_server_exceptions=False)

def login(c,who='supervisor'):
    r=c.post('/auth/login',json={'email':who+'@hesheng.local','password':'Fit2026!'})
    return {'Authorization':'Bearer '+r.json()['token']}

def payload():
    return {'note':'周末','windows':W,'buyers':[
        {'profile_id':1,'wait_since':'2026-11-10T09:00:00','windows':[0,1]},
        {'profile_id':2,'wait_since':'2026-11-12T09:00:00','windows':[0,1]}]}

class ApiTests(unittest.TestCase):
    def setUp(self):
        fresh();self.c=make_client();self.h=login(self.c)

    def test_create_and_read(self):
        r=self.c.post('/batches',json=payload(),headers=self.h);self.assertEqual(r.status_code,200,r.text)
        v=r.json();self.assertEqual(v['summary']['scheduled'],2)
        r=self.c.get('/batches',headers=login(self.c,'viewer'));self.assertEqual(r.status_code,200)

    def test_viewer_cannot_write(self):
        self.assertEqual(self.c.post('/batches',json=payload(),headers=login(self.c,'viewer')).status_code,403)
        self.c.post('/batches',json=payload(),headers=self.h)
        self.assertEqual(self.c.post('/batches/1/confirm',headers=login(self.c,'viewer')).status_code,403)
        self.assertEqual(self.c.post('/batches/1/replan',headers=login(self.c,'viewer')).status_code,403)

    def test_anonymous_rejected(self):
        self.assertEqual(self.c.get('/batches').status_code,401)

    def test_confirm_ok_and_idempotent(self):
        self.c.post('/batches',json=payload(),headers=self.h)
        r=self.c.post('/batches/1/confirm',headers=self.h);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['batch']['state'],'confirmed')
        r2=self.c.post('/batches/1/confirm',headers=self.h);self.assertEqual(r2.status_code,200)
        reg=self.c.get('/register',headers=self.h).json()
        self.assertEqual(len(reg['fittings']),2)

    def test_confirm_conflict_returns_409_versions_and_no_duplicate(self):
        self.c.post('/batches',json=payload(),headers=self.h)
        # 并发单笔预约占走 G1
        r=self.c.post('/fittings',json={'garment_id':1,'profile_id':3,'slot':1,'starts':W[0]['starts'],'ends':W[0]['ends']},headers=self.h)
        self.assertEqual(r.status_code,200,r.text)
        r=self.c.post('/batches/1/confirm',headers=self.h)
        self.assertEqual(r.status_code,409);self.assertIn('整批未落单',r.json()['detail'])
        v=self.c.get('/batches/1',headers=self.h).json()
        self.assertEqual(v['batch']['state'],'draft');self.assertEqual(v['batch']['plan_version'],2)
        # 重试：只补落剩余者，不重复留衣
        r=self.c.post('/batches/1/confirm',headers=self.h);self.assertEqual(r.status_code,200,r.text)
        reg=self.c.get('/register',headers=self.h).json()
        self.assertEqual(len(reg['fittings']),2)
        self.assertEqual(len({f['garment_id'] for f in reg['fittings']}),2)
        self.assertEqual(len(reg['batch_items']),4)  # 2 个版本 × 2 位买家

    def test_register_exposes_batches(self):
        self.c.post('/batches',json=payload(),headers=self.h)
        reg=self.c.get('/register',headers=self.h).json()
        for key in ('batches','batch_windows','batch_buyers','batch_items'):self.assertIn(key,reg)
        self.assertEqual(len(reg['batch_windows']),2)

    def test_invalid_payload_rejected(self):
        bad=payload();bad['windows']=[{'starts':'2026-11-21T10:00:00','ends':'2026-11-21T13:00:00'}]
        self.assertEqual(self.c.post('/batches',json=bad,headers=self.h).status_code,409)
        bad=payload();bad['buyers'][0]['windows']=[]
        self.assertEqual(self.c.post('/batches',json=bad,headers=self.h).status_code,409)

if __name__=='__main__':
    unittest.main(verbosity=2)
