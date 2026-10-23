import tempfile
import unittest
from pathlib import Path
from travel_job_collector.alba import extract_records, RobotsPolicy, CollectionError, main, listing_url


def fixture(rows, total=1):
    return f'<div id="NormalInfo"><table><tbody>{rows}</tbody></table><script>getWnSearchPaging(\'1\', \'{total}\', 50, 10, \'\')</script></div>'


def row(adid='123',area='제주 제주시',href=None):
    href=href or f'/job/Detail?adid={adid}&amp;listmenucd=SHORT'
    return f'''<tr data-imid="{adid}"><td class="title"><a class="info" href="{href}"><span class="company">예시 업체</span><span class="title">행사 &amp; 안내</span></a></td><td class="local">{area}</td><td class="data">09:00~18:00<br/>시간협의</td><td class="pay"><span class="payIcon">일급</span><span class="number">100,000</span></td><td class="regDate">오늘<a>요약보기</a></td></tr>'''


class AlbaTests(unittest.TestCase):
    def test_parser_and_dedup(self):
        records, meta=extract_records(fixture(row()+row()),fetched_at='fixed')
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['title'],'행사 & 안내')
        self.assertEqual(records[0]['posted_text'],'오늘')
        self.assertEqual(records[0]['pay_text'],'100,000원')
        self.assertFalse(records[0]['publishable'])
        self.assertEqual(records[0]['permission_status'],'UNKNOWN')
        self.assertIsNone(records[0]['public_contact_phone'])
        self.assertIsNone(records[0]['working_period_text'])

    def test_non_jeju_excluded(self):
        records,_=extract_records(fixture(row(area='서울 강남구')))
        self.assertEqual(records,[])
        records,_=extract_records(fixture(row(area='서귀포시 전체')))
        self.assertEqual(len(records),1)

    def test_missing_schema_and_mismatched_link_fail(self):
        for html in ['captcha',fixture('',total=2),fixture(row(href='/job/Detail?adid=456'))]:
            with self.assertRaises(CollectionError): extract_records(html)

    def test_empty_result(self):
        self.assertEqual(extract_records(fixture('',total=0))[0],[])

    def test_robots_longest_rule_escaped_lines(self):
        policy=RobotsPolicy(r'User-agent: *\r\nDisallow: /\r\nAllow: /$\r\nAllow: /job/\r\nDisallow: /job/private\r\n')
        self.assertTrue(policy.allowed(listing_url()))
        self.assertFalse(policy.allowed('https://www.alba.co.kr/job/private/1'))
        self.assertFalse(policy.allowed('https://www.alba.co.kr/resume/list'))

    def test_robot_specific_group(self):
        policy=RobotsPolicy('User-agent: *\nAllow: /\nUser-agent: TravelJobCollector\nDisallow: /')
        self.assertFalse(policy.allowed(listing_url()))

    def test_offline_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'list.html';out=Path(directory)/'out.jsonl'
            source.write_text(fixture(row()),encoding='utf-8')
            self.assertEqual(main(['--html',str(source),'--output',str(out)]),0)
            old=out.read_bytes();source.write_text('captcha')
            self.assertEqual(main(['--html',str(source),'--output',str(out)]),1)
            self.assertEqual(out.read_bytes(),old)
