import unittest
from travel_job_collector.idge import extract_records,enrich_detail,short_category,CollectionError,validate_url


def fixture(category='단기 1~7일',title='행사 운영',idx='123'):
    card=f'<a class="post_link_wrap" href="/parttime/?idx={idx}&amp;bmode=view"><div class="title-block"><em class="notice-block" style="display:none">공지</em><span><em>[{category}]</em></span>{title}<span class="icons">N</span></div><span class="writer">작성자</span><small class="date" title="2026-10-22 10:00">오늘</small></a>'
    return card+card+'<ul class="pagination"><li class="active">1</li><a href="/parttime/?page=2">2</a></ul>'


class IdgeTests(unittest.TestCase):
    def test_list_status_and_category(self):
        rows,meta,pages=extract_records(fixture())
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['title'],'행사 운영')
        self.assertEqual(rows[0]['working_period_text'],'[단기 1~7일]')
        self.assertIsNone(rows[0]['company_name'])
        self.assertFalse(rows[0]['publishable'])
        self.assertFalse(rows[0]['detail_collected'])
        self.assertEqual(meta['page'],1)
        self.assertIn(2,pages)

    def test_non_short_and_closed(self):
        for value in [fixture(category='장기'),fixture(title='행사 모집 마감')]:
            self.assertEqual(extract_records(value)[0],[])
        self.assertTrue(short_category('단기 2주 이내'))
        self.assertFalse(short_category('단기 3개월'))

    def test_detail_channels_and_no_permission(self):
        record=extract_records(fixture())[0][0]
        result=enrich_detail(record,'<div class="board_txt_area"><p>문자❌ 전화문의</p><a href="tel:064-000-0000">064-000-0000</a><p>example@example.com</p></div>')
        self.assertEqual(result['public_contact_phones'],['0640000000'])
        self.assertEqual(result['public_contact_emails'],['example@example.com'])
        self.assertEqual(result['sms_contact_policy'],'PROHIBITED')
        self.assertEqual(result['permission_status'],'UNKNOWN')
        self.assertFalse(result['publishable'])

    def test_missing_structure_and_external_rejected(self):
        with self.assertRaises(CollectionError): extract_records('login')
        with self.assertRaises(CollectionError): enrich_detail({},'login')
        for url in ['https://evil.test/parttime/','https://www.idge.co.kr/admin/','https://www.idge.co.kr/parttime/?bmode=write']:
            with self.assertRaises(CollectionError):validate_url(url)

    def test_keyword_does_not_replace_category(self):
        self.assertEqual(extract_records(fixture(category='장기',title='단기 행사'))[0],[])
