import json
import tempfile
import unittest
from pathlib import Path
from travel_job_collector.jobkorea import extract_records, main, JobkoreaClient
from travel_job_collector.albamon import CollectionError


def html(title='행사 3일 단기 알바',area='제주 제주시',extra='',local='N000'):
    condition=json.dumps({'local':local,'textinclude':'단기'})
    return f'''<div id="devSearchForm" data-value-json='{condition}'></div><div id="dev-gi-list"><table><tbody><tr data-gno="12345"><td class="tplCo"><a class="link">업체</a></td><td class="tplTit"><strong><a href="/Recruit/GI_Read/12345?sc=1">{title}</a></strong><p class="etc"><span class="cell">경력무관</span><span class="cell">학력무관</span><span class="cell">{area}</span><span class="cell">계약직</span><span class="cell">시급 12,000원</span></p></td><td class="odd"><span class="time">오늘 등록</span><span class="date">채용시 마감</span></td></tr>{extra}</tbody></table></div>'''

class JobkoreaTests(unittest.TestCase):
    def test_fields_and_status(self):
        rows,meta=extract_records(html());row=rows[0]
        self.assertEqual(row['source_id'],'12345');self.assertEqual(row['area'],'제주 제주시')
        self.assertEqual(row['pay_text'],'12,000원');self.assertFalse(row['publishable'])
        self.assertIsNone(row['public_contact_phone']);self.assertEqual(meta['scanned'],1)
    def test_keyword_requires_option(self):
        self.assertEqual(extract_records(html(title='단기 알바'))[0],[])
        self.assertEqual(len(extract_records(html(title='단기 알바'),include_keywords=True)[0]),1)
    def test_long_term_excluded_even_with_keyword(self):
        for title in ['단기 알바 2개월','단기 3~6개월','단기 1개월~3개월']:
            self.assertEqual(extract_records(html(title=title),include_keywords=True)[0],[])
    def test_outside_region_and_unrelated(self):
        self.assertEqual(extract_records(html(area='서울 강남구'))[0],[])
        self.assertEqual(extract_records(html(title='단기채권 상담'),include_keywords=True)[0],[])
    def test_wrong_context_fails(self):
        with self.assertRaises(CollectionError):extract_records(html(local='I000'))
        with self.assertRaises(CollectionError):extract_records('<html>로그인</html>')
    def test_empty_requires_message(self):
        context=html().split('<div id="dev-gi-list">')[0]
        self.assertEqual(extract_records(context+'<div id="dev-gi-list">검색 결과가 없습니다</div>')[0],[])
        with self.assertRaises(CollectionError):extract_records(context+'<div id="dev-gi-list"></div>')
    def test_offline_failure_preserves_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'bad.html';source.write_text('blocked');output=Path(tmp)/'out.jsonl';output.write_text('keep')
            self.assertEqual(main(['--html',str(source),'--output',str(output)]),1)
            self.assertEqual(output.read_text(),'keep')
    def test_client_rejects_private_path(self):
        with self.assertRaises(CollectionError):JobkoreaClient().fetch('https://www.jobkorea.co.kr/User/Login')

    def test_external_listing_skipped(self):
        document=html().replace('/Recruit/GI_Read/12345?sc=1','/Recruit/GI_Read/12345/Ext?siteCode=WN')
        self.assertEqual(extract_records(document)[0],[])
