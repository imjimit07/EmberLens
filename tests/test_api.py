from pathlib import Path
from tempfile import TemporaryDirectory
import importlib
import os

from fastapi.testclient import TestClient
from backend.core import connect

CSV="""latitude,longitude,acq_date,acq_time,satellite,instrument,confidence,frp,version
30.1,75.1,2025-11-03,0930,Terra,MODIS,82,11,6.1
30.2,75.2,2025-11-04,1020,Aqua,MODIS,73,9,6.1
"""


def test_import_calendar_map_and_month():
    with TemporaryDirectory() as temp:
        os.environ['EMBERLENS_DB']=str(Path(temp)/'data.sqlite')
        from backend import main
        importlib.reload(main)
        client=TestClient(main.app)
        assert client.get('/api/health').json()['status']=='ok'
        u=client.post('/api/import/csv?source=MODIS_SP&filename=modis.csv',content=CSV,headers={'content-type':'text/csv'})
        assert u.status_code==200,u.text
        assert u.json()['rows_added']==2
        q='region=punjab&start=2025-11-01&end=2025-11-30'
        s=client.get('/api/summary?'+q).json()
        assert s['total_detections']==2
        assert s['observed_positive_days']==2
        c=client.get('/api/calendar?'+q).json()
        assert c['rows'][0]['month']=='2025-11'
        assert c['rows'][0]['detections']==2
        p=client.get('/api/points?'+q).json()
        assert len(p['points'])==2
        detail=client.get('/api/month-detail?region=punjab&month=2025-11').json()
        assert detail['by_sensor']['MODIS']['detections']==2
        assert client.get('/api/summary?region=california&start=2025-11-01&end=2025-11-30').json()['total_detections']==0
        assert client.get('/').status_code==200
        assert client.post('/api/import/firms',json={'region':'punjab','source':'MODIS_SP','start_date':'2025-01-01','days':6}).status_code==422
        os.environ.pop('EMBERLENS_DB',None)
