import unittest
from travel_job_collector.daangn import extract_records, short_term_reason, CollectionError, listing_url
from travel_job_collector.alba import RobotsPolicy


def fixture(schedule='총 2일 / 10월 27~28일 · 09:00 ~ 18:00',title='행사 안내',heading='제주특별자치도 제주시 도남동에서 찾는 알바',tags=('예시 업체','법환동')):
    labels=''.join(f'<span class="seed-tag-group-item__label">{tag}</span>' for tag in tags)
    card=f'<a href="/job-posts/example-abcdefgh1234"><h3>{title}</h3><span class="seed-text">일급 100,000원</span><span class="seed-text">{schedule}</span>{labels}</a>'
    return f'<html><title>{heading}</title><main>{card}{card}</main></html>'


class DaangnTests(unittest.TestCase):
    def test_parse_dedup_and_status(self):
        records,summary=extract_records(fixture())
        self.assertEqual(len(records),1)
        item=records[0]
        self.assertEqual(item['source_id'],'abcdefgh1234')
        self.assertEqual(item['company_name'],'예시 업체')
        self.assertEqual(item['area'],'법환동')
        self.assertFalse(item['publishable'])
        self.assertEqual(item['permission_status'],'UNKNOWN')
        self.assertIsNone(item['public_contact_phone'])
        self.assertEqual(summary['scope'],'initial_public_html_only')

    def test_single_locality_no_fake_company(self):
        records,_=extract_records(fixture(tags=('삼도2동',)))
        self.assertIsNone(records[0]['company_name'])

    def test_repeating_weekly_excluded(self):
        self.assertEqual(extract_records(fixture(schedule='월~금 · 09:00 ~ 18:00'))[0],[])
        self.assertIsNone(short_term_reason('총 40일 / 10월~12월 · 시간협의','단기'))

    def test_relative_date_and_keyword(self):
        self.assertIsNotNone(short_term_reason('내일(금) · 08:00 ~ 17:00','짐 운반'))
        self.assertIsNone(short_term_reason('시간협의','단기행사'))
        self.assertEqual(short_term_reason('시간협의','단기행사',True),'keyword_only_duration_needs_review')

    def test_wrong_region_and_missing_structure(self):
        for html in ['captcha',fixture(heading='서울에서 찾는 알바'),'<title>제주특별자치도 알바</title><main></main>']:
            with self.assertRaises(CollectionError):extract_records(html)

    def test_closed_excluded(self):
        self.assertEqual(extract_records(fixture(title='공고 마감'))[0],[])

    def test_no_api_or_apply_robots(self):
        p=RobotsPolicy('User-agent: *\nDisallow: /api/\nDisallow: /job-posts/*/apply\nAllow: /')
        self.assertTrue(p.allowed(listing_url('6984')))
        self.assertFalse(p.allowed('https://jobs.daangn.com/api/jobs'))
        self.assertFalse(p.allowed('https://jobs.daangn.com/job-posts/abc/apply'))
