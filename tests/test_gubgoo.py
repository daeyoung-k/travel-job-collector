import json
import unittest
from datetime import datetime, timezone
from travel_job_collector.gubgoo import extract_records
from travel_job_collector.albamon import CollectionError


def html(items, page=1):
    data='4:'+json.dumps({'initialWorkListData':{'list':items,'currentPage':page,'totalPage':1}},ensure_ascii=False)
    cut=len(data)//2
    return ''.join('<script>self.__next_f.push('+json.dumps([1,s])+')</script>' for s in [data[:cut],data[cut:]])


def job():
    return {'id':'a'*24,'title':'행사 지원','offer':{'name':'업체'},'addressInfo':{'region1':'제주','region2':'제주시','full':'제주 제주시'},'wage':{'hourly':12000,'daily':24000,'type':'시급'},'startAt':{'Y':2026,'M':11,'D':4,'h':23,'m':0},'endAt':{'Y':2026,'M':11,'D':5,'h':1,'m':0},'content':'공개 본문','breakTime':{'minutes':0,'type':'무급'}}

class GubgooTests(unittest.TestCase):
    def extract(self, items, **kw):
        return extract_records(html(items),now=datetime(2026,10,22,tzinfo=timezone.utc),**kw)
    def test_split_rsc_and_midnight(self):
        rows,meta=self.extract([job(),job()]); self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['work_end_at'],'2026-11-05T01:00:00+09:00')
        self.assertFalse(rows[0]['publishable']); self.assertIsNone(rows[0]['public_contact_phone'])
    def test_empty_is_success(self):
        self.assertEqual(self.extract([])[0],[])
    def test_missing_data_fails(self):
        with self.assertRaises(CollectionError): extract_records('<html>blocked</html>')
    def test_other_region_fails(self):
        value=job();value['addressInfo']['region1']='서울'
        with self.assertRaises(CollectionError): self.extract([value])
    def test_ended_skipped(self):
        value=job();value['startAt']['M']=9;value['endAt']['M']=9
        self.assertEqual(self.extract([value])[0],[])
        self.assertEqual(len(self.extract([value],include_ended=True)[0]),1)
    def test_bad_page_fails(self):
        with self.assertRaises(CollectionError): extract_records(html([],page=2),page=1)
