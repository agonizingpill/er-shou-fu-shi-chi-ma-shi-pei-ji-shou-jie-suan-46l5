from src.database import Base,engine,Session
from src.models import *
from src.security import password_hash
from src.common import rows

def seed():
 Base.metadata.create_all(engine)
 with Session() as s:
  for r in ['operator','supervisor','viewer']:
   if not any(u.email==r+'@hesheng.local' for u in rows(s,User)):s.add(User(email=r+'@hesheng.local',password=password_hash('Fit2026!'),role=r))
  if not s.get(Shop,1):s.add(Shop(id=1,now='2026-11-18T09:00:00'))
  if not any(g.code=='G031' for g in rows(s,Garment)):
   c=Consignor(name='林禾',contact='柜台留存联系单LH031');s.add(c);s.flush();prof=FitProfile(code='P031',chest=88,shoulder=37);s.add(prof);s.flush()
   for code,ends in [('A031','2026-11-26T18:00:00'),('A032','2026-11-20T18:00:00'),('A033','2026-11-17T18:00:00')]:s.add(Agreement(code=code,consignor_id=c.id,floor_cents=18000,commission_bp=2000,ends=ends))
   s.flush();ags=rows(s,Agreement)
   for code,title,ag,state in [('G031','烟苔绿短外套',ags[0],'listed'),('G032','复检中的棉衬衣',ags[1],'inspection'),('G033','期满未售针织衫',ags[2],'listed'),('G034','赭色直筒外套',ags[0],'listed')]:
    g=Garment(code=code,title=title,agreement_id=ag.id,condition='八成新，领口轻微使用痕迹，无破损',price_cents=22000,state=state);s.add(g);s.flush();s.add(Measurement(garment_id=g.id,chest=96,shoulder=39,length=58,elasticity=.2,cut=1))
   s.flush();g=next(g for g in rows(s,Garment) if g.code=='G032');f=Fitting(garment_id=g.id,profile_id=prof.id,slot=1,starts='2026-11-16T15:00:00',ends='2026-11-16T16:00:00',state='completed');s.add(f);s.flush();sale=Sale(fitting_id=f.id,garment_id=g.id,price_cents=22000,commission_bp=2000,commission_cents=4400,net_cents=17600,sold_at='2026-11-16T15:10:00',return_deadline='2026-11-23T15:10:00',state='returned');s.add(sale);s.flush();s.add(Return(sale_id=sale.id,reason='买家反馈肩部偏紧，已按原样退回待复检'))
  _weekend(s)
  s.commit()

def _weekend(s):
 if any(g.code=='G035' for g in rows(s,Garment)):return
 ag=next((a for a in rows(s,Agreement) if a.code=='A031'),rows(s,Agreement)[0] if rows(s,Agreement) else None)
 if not ag:return
 for code,title,ch,sh,e,cut in [('G035','燕麦色羊毛开衫',100,41,.4,2),('G036','靛蓝弹力高领衫',92,38,.6,0),('G037','松果棕阔领大衣',104,42,.3,2)]:
  g=Garment(code=code,title=title,agreement_id=ag.id,condition='九成新，无污渍无破损，纽扣齐全',price_cents=26000,state='listed');s.add(g);s.flush();s.add(Measurement(garment_id=g.id,chest=ch,shoulder=sh,length=60,elasticity=e,cut=cut))
 for code,ch,sh in [('P032',92,38),('P033',102,43),('P034',85,36),('P035',80,35)]:s.add(FitProfile(code=code,chest=ch,shoulder=sh))
 s.flush()
 if rows(s,FittingBatch):return
 from src import batch_service
 from src.common import time as _time
 profiles={p.code:p.id for p in rows(s,FitProfile)}
 now=_time(s.get(Shop,1).now) if s.get(Shop,1) else _time('2026-11-18T09:00:00')
 windows=[('2026-11-21T10:00:00','2026-11-21T11:00:00'),('2026-11-21T13:00:00','2026-11-21T14:00:00'),('2026-11-21T15:00:00','2026-11-21T16:00:00'),('2026-11-22T11:00:00','2026-11-22T12:00:00'),('2026-11-22T14:00:00','2026-11-22T15:00:00')]
 if any(_time(w[0])<now for w in windows):return
 buyers=[{'profile_id':profiles['P033'],'wait_since':'2026-11-01T09:30:00','windows':[0,1,2,3,4]},
         {'profile_id':profiles['P035'],'wait_since':'2026-11-08T14:00:00','windows':[0,1,3]},
         {'profile_id':profiles['P032'],'wait_since':'2026-11-10T10:20:00','windows':[0,2,4]},
         {'profile_id':profiles['P031'],'wait_since':'2026-11-15T09:10:00','windows':[1,2]},
         {'profile_id':profiles['P034'],'wait_since':'2026-11-16T16:40:00','windows':[3,4]}]
 batch_service.create(s,{'note':'周末候补试衣预排（演示）','windows':[{'starts':a,'ends':b} for a,b in windows],'buyers':buyers},'supervisor@hesheng.local')
 s.flush()
if __name__=='__main__':seed()
