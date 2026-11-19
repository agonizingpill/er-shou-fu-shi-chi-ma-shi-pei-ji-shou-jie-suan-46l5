"""周末批量试衣排班的后端规则测试（SQLite 临时库，不依赖 Docker）。

运行：cd /workspace/backend && DATABASE_URL=sqlite:////tmp/x.db PYTHONPATH=. python3 ../test-artifacts/gb-1039/test_batch_service.py
"""
import os, sys, tempfile, unittest
os.environ.setdefault('DATABASE_URL','sqlite:///'+tempfile.mkdtemp()+'/t.db')
os.environ.setdefault('REDIS_URL','redis://127.0.0.1:6379/0')
sys.path.insert(0,os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','backend')))

from src.database import Base,engine,Session
from src.models import *
from src.common import rows
from src import batch_service as bs
from src import fitting_service as fs

W=[('2026-11-21T10:00:00','2026-11-21T11:00:00'),
   ('2026-11-21T11:00:00','2026-11-21T12:00:00'),
   ('2026-11-22T14:00:00','2026-11-22T15:00:00')]

def setup_db(garments=None,profiles=None,ends='2026-11-30T18:00:00'):
    Base.metadata.drop_all(engine);Base.metadata.create_all(engine)
    garments=garments or [('G1',96,39,'listed'),('G2',96,39,'listed')]
    profiles=profiles or [('P1',88,37),('P2',88,37),('P3',102,43)]
    with Session() as s:
        s.add(Shop(id=1,now='2026-11-18T09:00:00'))
        s.add(Consignor(id=1,name='林禾',contact='x'));s.flush()
        s.add(Agreement(id=1,code='A1',consignor_id=1,floor_cents=18000,commission_bp=2000,ends=ends));s.flush()
        for i,(code,ch,sh,state) in enumerate(garments,1):
            s.add(Garment(id=i,code=code,title='衣'+code,agreement_id=1,condition='八成新，无破损',price_cents=22000,state=state));s.flush()
            s.add(Measurement(garment_id=i,chest=ch,shoulder=sh,length=58,elasticity=.2,cut=1))
        for i,(code,ch,sh) in enumerate(profiles,1):s.add(FitProfile(id=i,code=code,chest=ch,shoulder=sh))
        s.commit()

def payload(buyers=None,windows=None,note='测试'):
    buyers=buyers or [{'profile_id':1,'wait_since':'2026-11-10T09:00:00','windows':[0,1]},
                      {'profile_id':2,'wait_since':'2026-11-12T09:00:00','windows':[0,1]}]
    windows=windows or [{'starts':a,'ends':b} for a,b in W]
    return {'note':note,'windows':windows,'buyers':buyers}

def create(p=None):
    with Session() as s:
        v=bs.create(s,p or payload(),'op@x');s.commit();return v

class BatchTests(unittest.TestCase):
    def test_two_buyers_two_garments_both_scheduled(self):
        setup_db();v=create()
        self.assertEqual(v['summary'],{'scheduled':2,'unscheduled':0})
        self.assertEqual(len({x['garment_id'] for x in v['items']}),2)

    def test_preplan_holds_nothing(self):
        setup_db();v=create()
        with Session() as s:
            self.assertFalse(rows(s,Fitting))
            self.assertTrue(all(g.state=='listed' for g in rows(s,Garment)))
            self.assertEqual(rows(s,FittingBatch)[0].state,'draft')

    def test_priority_max_count_before_waiting(self):
        # P3 无合身衣物；P1/P2 都能排上，两人都排上而非只排一个等得久的
        setup_db();v=create()
        self.assertEqual(v['summary']['scheduled'],2)

    def test_longer_wait_preferred_when_scarce(self):
        # 一件合身衣物、两个买家，等更久的 P1 拿到
        setup_db(garments=[('G1',96,39,'listed')])
        v=create()
        items={x['buyer_id']:x for x in v['items']}
        self.assertEqual(v['summary']['scheduled'],1)
        self.assertEqual(items[1]['state'],'scheduled')
        self.assertEqual(items[2]['state'],'unscheduled')
        self.assertIn('试衣位',items[2]['reason'])

    def test_same_slot_no_overlap_across_windows(self):
        setup_db(profiles=[('P1',88,37),('P2',88,37),('P3',85,36)])
        v=create(payload(buyers=[{'profile_id':i,'wait_since':'2026-11-1%dT09:00:00'%i,'windows':[0,1]} for i in (1,2,3)]))
        # 两件合身衣物各只能留给一人，第三人因衣物有限未排上
        self.assertEqual(v['summary']['scheduled'],2)
        slots=[(x['starts'],x['slot']) for x in v['items'] if x['state']=='scheduled']
        self.assertEqual(len(slots),len(set(slots)))
        self.assertEqual(len({x['garment_id'] for x in v['items'] if x['state']=='scheduled'}),2)

    def test_unscheduled_reason_for_no_fit(self):
        setup_db();v=create(payload(buyers=[{'profile_id':3,'wait_since':'2026-11-01T09:00:00','windows':[0,1,2]}]))
        it=v['items'][0]
        self.assertEqual(it['state'],'unscheduled')
        self.assertIn('尺码模型',it['reason'])

    def test_empty_window_selection_rejected(self):
        setup_db()
        with Session() as s:
            with self.assertRaises(Exception) as ctx:bs.create(s,payload(buyers=[{'profile_id':1,'wait_since':'2026-11-10T09:00:00','windows':[]}]),'op')
        self.assertIn('至少选择',str(ctx.exception))

    def test_confirm_creates_fittings_and_holds(self):
        setup_db();v=create()
        with Session() as s:
            b=s.get(FittingBatch,1);out,err=bs.confirm(s,1);s.commit()
        self.assertIsNone(err)
        self.assertEqual(out['batch']['state'],'confirmed')
        with Session() as s:
            fs_=rows(s,Fitting)
            self.assertEqual(len(fs_),2)
            self.assertTrue(all(f.state=='reserved' for f in fs_))
            self.assertTrue(all(g.state=='held' for g in rows(s,Garment)))
            self.assertEqual({x.state for x in rows(s,BatchItem)}, {'confirmed'})

    def test_confirm_idempotent(self):
        setup_db();v=create()
        with Session() as s:
            bs.confirm(s,1);s.commit()
        with Session() as s:
            out,err=bs.confirm(s,1);s.commit()
        with Session() as s:
            self.assertEqual(len(rows(s,Fitting)),2)
            self.assertEqual(out['batch']['state'],'confirmed')

    def test_confirm_conflict_replans_without_booking(self):
        setup_db();v=create()
        # 单笔预约并发占走衣物 G1 的时段
        with Session() as s:
            fs.reserve(s,{'garment_id':1,'profile_id':3,'slot':1,'starts':W[0][0],'ends':W[0][1]});s.commit()
        with Session() as s:
            with self.assertRaises(bs.Replanned) as ctx:bs.confirm(s,1)
            s.commit()
        self.assertIn('整批未落单',str(ctx.exception))
        with Session() as s:
            fittings=rows(s,Fitting)
            self.assertEqual(len(fittings),1)  # 只有并发的那一笔
            b=s.get(FittingBatch,1)
            self.assertEqual(b.state,'draft');self.assertEqual(b.plan_version,2)
            v2=bs.view(s,b)
            self.assertEqual(v2['summary']['scheduled'],1)
            lost=[x for x in v2['items'] if x['state']=='unscheduled']
            self.assertTrue(any('并发冲突' in x['reason'] for x in lost))
        # 第二次确认成功：只落剩余可排的一单，且不重复留衣
        with Session() as s:
            out,err=bs.confirm(s,1);s.commit()
        self.assertIsNone(err)
        with Session() as s:
            fittings=rows(s,Fitting)
            self.assertEqual(len(fittings),2)
            self.assertEqual(len({f.garment_id for f in fittings}),2)
            self.assertEqual(out['batch']['state'],'confirmed')

    def test_replan_version_history(self):
        setup_db();v=create()
        with Session() as s:
            bs.replan(s,1);s.commit()
        with Session() as s:
            b=s.get(FittingBatch,1)
            self.assertEqual(b.plan_version,2)
            self.assertEqual(len([x for x in rows(s,BatchItem) if x.version==1]),2)
            self.assertEqual(len([x for x in BatchItem.__table__.c and rows(s,BatchItem) if x.version==2]),2)

    def test_confirm_other_slot_conflict_detected(self):
        setup_db();v=create()
        with Session() as s:
            fs.reserve(s,{'garment_id':2,'profile_id':3,'slot':2,'starts':W[0][0],'ends':W[0][1]});s.commit()
        with Session() as s:
            with self.assertRaises(bs.Replanned):bs.confirm(s,1)
            s.commit()

    def test_windows_past_rejected(self):
        setup_db()
        with Session() as s:
            with self.assertRaises(Exception):bs.create(s,payload(windows=[{'starts':'2026-11-01T10:00:00','ends':'2026-11-01T11:00:00'}]),'op')

    def test_window_duration_limit(self):
        setup_db()
        with Session() as s:
            with self.assertRaises(Exception):bs.create(s,payload(windows=[{'starts':'2026-11-21T10:00:00','ends':'2026-11-21T13:00:00'}]),'op')

    def test_window_beyond_agreement_skipped(self):
        setup_db(ends='2026-11-21T10:30:00')
        v=create()
        self.assertEqual(v['summary']['scheduled'],0)
        self.assertTrue(all('寄售期' in x['reason'] or '时段' in x['reason'] or '试衣位' in x['reason'] for x in v['items']))

    def test_transitive_overlap_not_double_booked(self):
        # A 10-11 / B 10:30-11:30 / C 11-12：A 与 C 不重叠，只应 A、C 共用同一试衣位
        setup_db(garments=[('G1',96,39,'listed'),('G2',96,39,'listed')])
        wins=[('2026-11-21T10:00:00','2026-11-21T11:00:00'),
              ('2026-11-21T10:30:00','2026-11-21T11:30:00'),
              ('2026-11-21T11:00:00','2026-11-21T12:00:00')]
        buyers=[{'profile_id':i,'wait_since':'2026-11-1%dT09:00:00'%i,'windows':[0,1,2]} for i in (1,2)]
        v=create(payload(buyers=buyers,windows=[{'starts':a,'ends':b} for a,b in wins]))
        self.assertEqual(v['summary']['scheduled'],2)
        got=sorted((x['starts'],x['slot']) for x in v['items'] if x['state']=='scheduled')
        # 两人不能同时落在相互重叠的窗口
        starts=[g[0] for g in got]
        self.assertFalse(any(s1<s2<W[0][1] for s1,s2 in [(starts[0],starts[1]),(starts[1],starts[0])] if s1<s2))
        # 确认时不触发冲突
        with Session() as s:
            bs.confirm(s,1);s.commit()

if __name__=='__main__':
    unittest.main(verbosity=2)
