import json
import tempfile
import unittest
from pathlib import Path
from travel_job_collector.saramin import extract_records, SaraminClient, main
from travel_job_collector.albamon import CollectionError


def html(title='팝업 단기 알바 (7일)',area='제주 제주시',loc='116000',closing='~10.30(금)',items=True):
    params=json.dumps(json.dumps({'action':'domestic','loc_mcd':loc,'searchword':'단기','page':1}))
    row=f'''<div class="list_item" id="rec-12345"><div class="company_nm"><a class="str_tit">업체</a></div><div class="job_tit"><a href="/zf_user/jobs/relay/view?rec_idx=12345">{title}</a></div><p class="work_place">{area}</p><p class="career">경력무관 · 아르바이트</p><div class="support_detail"><span class="date">{closing}</span><span class="deadlines">오늘 등록</span></div></div>'''
    return f'''<script>var config={{'params': {params}}};</script><div id="default_list_wrap"><div class="list_body">{row if items else '검색 결과가 없습니다'}</div></div>'''

class SaraminTests(unittest.TestCase):
    def test_fields_and_status(self):
        rows,meta=extract_records(html());r=rows[0]
        self.assertEqual(r['source_id'],'12345');self.assertEqual(r['source'],'saramin')
        self.assertFalse(r['publishable']);self.assertIsNone(r['public_contact_phone'])
        self.assertEqual(meta['scanned'],1)
    def test_keyword_option(self):
        self.assertEqual(extract_records(html(title='단기 알바'))[0],[])
        self.assertEqual(len(extract_records(html(title='단기 알바'),include_keywords=True)[0]),1)
    def test_long_or_unrelated_excluded(self):
        for title in ['단기 알바 2개월','장애인 단기거주시설 정규직 채용','단기채권 상담직']:
            self.assertEqual(extract_records(html(title=title),include_keywords=True)[0],[])
    def test_wrong_region(self):
        self.assertEqual(extract_records(html(area='서울 강남구'))[0],[])
        with self.assertRaises(CollectionError):extract_records(html(loc='101000'))
    def test_missing_and_empty(self):
        self.assertEqual(extract_records(html(items=False))[0],[])
        with self.assertRaises(CollectionError):extract_records('<html>blocked</html>')
    def test_closed_and_bad_link(self):
        self.assertEqual(extract_records(html(closing='마감'))[0],[])
        with self.assertRaises(CollectionError):extract_records(html().replace('rec_idx=12345','rec_idx=77777'))
    def test_dedup_and_highlight(self):
        document=html(title='팝업 <strong>단기</strong> 알바 7일');part=document[document.index('<div class="list_item"'):document.index('</div></div>',document.index('<span class="deadlines"'))+6]
        rows,_=extract_records(document.replace('</script>','</script>'+part))
        self.assertEqual(len(rows),1);self.assertNotIn('<strong>',rows[0]['title'])
    def test_private_path_rejected(self):
        with self.assertRaises(CollectionError):SaraminClient().fetch('https://www.saramin.co.kr/zf_user/auth')
    def test_invalid_policy_rejected(self):
        from unittest.mock import patch
        with patch.object(SaraminClient,'fetch',return_value='<meta http-equiv="refresh">'):
            with self.assertRaises(CollectionError):SaraminClient().load_robots()
    def test_error_preserves_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'bad.html';source.write_text('bad');out=Path(tmp)/'out.jsonl';out.write_text('keep')
            self.assertEqual(main(['--html',str(source),'--output',str(out)]),1)
            self.assertEqual(out.read_text(),'keep')
    def test_company_without_profile_link(self):
        document=html().replace('<a class="str_tit">업체</a>','<span class="str_tit">업체</span>')
        self.assertEqual(extract_records(document)[0][0]['company_name'],'업체')
