import json
import tempfile
import unittest
from pathlib import Path
from travel_job_collector.albamon import CollectionError, extract_records, main, short_term_reason


def html(items, page=1):
    data = {'props': {'pageProps': {'dehydratedState': {'queries': [
        {'state': {'data': {'base': {'pagination': {'page': page, 'totalCount': len(items)}, 'normal': {'collection': items}}}}}
    ]}}}}
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + '</script>'


def row(**overrides):
    item = dict(recruitNo=123, recruitTitle='행사 도우미', workingPeriod='하루(1일)', workplaceArea='제주 제주시',
                workplaceAddress='제주 제주시 예시로', pay='100,000원', payType={'description':'일급'},
                applicationTypes=[{'description':'이메일지원'}], managerPhoneNumber='', postStatusType={'key':'POSTING'})
    item.update(overrides)
    return item


class ParserTests(unittest.TestCase):
    def test_extract_and_deduplicate(self):
        records, meta = extract_records(html([row(), row()]), fetched_at='fixed')
        self.assertEqual(len(records), 1)
        self.assertFalse(records[0]['publishable'])
        self.assertEqual(records[0]['permission_status'], 'UNKNOWN')
        self.assertEqual(records[0]['contact_status'], 'NOT_CONTACTED')
        self.assertFalse(records[0]['sms_application_listed'])
        self.assertIsNone(records[0]['public_contact_phone'])
        self.assertEqual(meta['scanned'], 2)

    def test_closed_long_term_non_jeju_excluded(self):
        records, _ = extract_records(html([row(postStatusType={'key':'CLOSED'}), row(recruitNo=2, workingPeriod='1년이상'), row(recruitNo=3, workplaceArea='서울', workplaceAddress='서울 강남구')]))
        self.assertEqual(records, [])

    def test_keyword_requires_explicit_option(self):
        self.assertIsNone(short_term_reason('기간협의', '단기 행사'))
        self.assertEqual(short_term_reason('기간협의', '단기 행사', True), 'keyword_only_duration_needs_review')
        self.assertIsNone(short_term_reason('6개월~1년', '카페'))

    def test_changed_structure_fails(self):
        for value in ['captcha', '<script id="__NEXT_DATA__">{}</script>']:
            with self.assertRaises(CollectionError): extract_records(value)

    def test_empty_listing_is_valid(self):
        records, _ = extract_records(html([]))
        self.assertEqual(records, [])

    def test_public_phone_not_approval(self):
        records, _ = extract_records(html([row(managerPhoneNumber='064-000-0000', applicationTypes=[{'description':'간편문자지원'}])]))
        self.assertTrue(records[0]['sms_application_listed'])
        self.assertFalse(records[0]['publishable'])

    def test_offline_cli_and_no_overwrite_on_error(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'list.html'; output = Path(directory)/'result.jsonl'
            source.write_text(html([row()]), encoding='utf-8')
            self.assertEqual(main(['--html', str(source), '--output', str(output)]), 0)
            old = output.read_bytes()
            source.write_text('captcha')
            self.assertEqual(main(['--html', str(source), '--output', str(output)]), 1)
            self.assertEqual(output.read_bytes(), old)

if __name__ == '__main__': unittest.main()
