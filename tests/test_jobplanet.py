import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from travel_job_collector.jobplanet import extract_records, JobplanetClient, main, BASE
from travel_job_collector.albamon import CollectionError
from travel_job_collector.alba import RobotsPolicy


def job():
    return {'@type':'JobPosting','title':'행사 단기 알바 7일','url':BASE+'/job/search?posting_ids%5B%5D=12345','hiringOrganization':{'name':'업체'},'jobLocation':{'@type':'Place','address':{'addressRegion':'제주특별자치도','addressLocality':'제주시','streetAddress':'제주대로 1'}},'description':'<p>행사 운영</p>','validThrough':'2026-11-30','datePosted':'2026-10-20','employmentType':'TEMPORARY'}


def html(value):
    return '<script type="application/ld+json">'+json.dumps(value,ensure_ascii=False)+'</script>'

class JobplanetTests(unittest.TestCase):
    def extract(self,value,**kw):
        return extract_records(html(value),now=datetime(2026,10,22,tzinfo=timezone.utc),**kw)
    def test_fields_and_status(self):
        rows,_=self.extract(job());r=rows[0]
        self.assertEqual(r['source_id'],'12345');self.assertFalse(r['publishable']);self.assertEqual(r['permission_status'],'UNKNOWN')
        self.assertIsNone(r['public_contact_phone']);self.assertEqual(r['body_text'],'행사 운영')
    def test_nested_graph_list_and_dedup(self):
        rows,_=self.extract({'@graph':[job(),{'@type':'ItemList','itemListElement':[{'item':job()}]}]})
        self.assertEqual(len(rows),1)
    def test_keyword_requires_option(self):
        value=job();value['title']='단기 알바'
        self.assertEqual(self.extract(value)[0],[])
        self.assertEqual(len(self.extract(value,include_keywords=True)[0]),1)
    def test_longterm_outside_and_missing_location(self):
        value=job();value['title']='단기 알바 2개월';self.assertEqual(self.extract(value,include_keywords=True)[0],[])
        value=job();value['jobLocation']['address']['addressRegion']='서울';self.assertEqual(self.extract(value)[0],[])
        value=job();value.pop('jobLocation');self.assertEqual(self.extract(value)[0],[])
    def test_expired(self):
        value=job();value['validThrough']='2026-10-01';self.assertEqual(self.extract(value)[0],[])
    def test_unverified_data_fails(self):
        with self.assertRaises(CollectionError):extract_records('<html>Access Denied</html>')
        with self.assertRaises(CollectionError):extract_records(html({'@type':'WebPage'}))
    def test_wrong_source_and_invalid_date(self):
        value=job();value['url']='https://example.com/job/12345'
        with self.assertRaises(CollectionError):self.extract(value)
        value=job();value['validThrough']='unknown'
        with self.assertRaises(CollectionError):self.extract(value)
    def test_private_path(self):
        with self.assertRaises(CollectionError):JobplanetClient().fetch(BASE+'/users')
    def test_403_stops(self):
        client=JobplanetClient();client.policy=RobotsPolicy('User-agent: *\nAllow: /')
        with patch('travel_job_collector.jobplanet.build_opener') as opener:
            opener.return_value.open.side_effect=HTTPError(BASE+'/job/search',403,'Forbidden',{},None)
            with self.assertRaisesRegex(CollectionError,'403'):client.fetch(BASE+'/job/search')
            self.assertEqual(opener.return_value.open.call_count,1)
    def test_error_preserves_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'blocked.html';path.write_text('blocked');out=Path(tmp)/'out.jsonl';out.write_text('keep')
            self.assertEqual(main(['--html',str(path),'--output',str(out)]),1);self.assertEqual(out.read_text(),'keep')
