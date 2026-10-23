"""Collect first-page public Jobkorea Jeju short-work candidates."""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

from bs4 import BeautifulSoup
from .alba import NoRedirect, RobotsPolicy
from .albamon import CollectionError, USER_AGENT, write_jsonl

BASE = 'https://www.jobkorea.co.kr'
# Do not match financial "단기채권" or weekly recurrence as a contract duration.
KEYWORDS = re.compile(r'단기(?!채권|금융|대출)|당일|하루|일일\s*(?:알바|근무)|행사\s*(?:알바|스태프|스탭|지원|운영)')
PERIOD = re.compile(r'(?<![\d주])(?:(\d+)\s*(?:개월|달|주|일)?\s*[~〜～\-]\s*)?(\d+)\s*(개월|달|주|일)(?!\s*전\s*등록)')


def listing_url():
    return BASE + '/recruit/joblist?' + urlencode({'local':'N000','menucode':'local','textinclude':'단기'})


class JobkoreaClient:
    def __init__(self, delay=3.0):
        self.delay, self.last, self.policy = delay, 0.0, None

    def fetch(self, url, *, robots=False):
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.netloc != 'www.jobkorea.co.kr' or parts.path not in {'/recruit/joblist','/robots.txt'}:
            raise CollectionError('잡코리아 공개 목록 경로만 허용합니다.')
        if robots and parts.path != '/robots.txt':
            raise CollectionError('정책 파일 경로가 아닙니다.')
        if not robots and (not self.policy or not self.policy.allowed(url)):
            raise CollectionError('robots.txt에 의해 요청을 중단했습니다.')
        wait = self.delay - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()
        try:
            request = Request(url, headers={'User-Agent':USER_AGENT,'Accept':'text/html,text/plain'})
            with build_opener(NoRedirect).open(request, timeout=30) as response:
                body = response.read(8_000_001)
                if len(body) > 8_000_000:
                    raise CollectionError('응답 크기 제한 초과')
                return body.decode(response.headers.get_content_charset() or 'utf-8')
        except (HTTPError, URLError, UnicodeError, LookupError) as exc:
            raise CollectionError(f'요청 실패, 재시도하지 않음: {exc}') from exc

    def load_robots(self):
        self.policy = RobotsPolicy(self.fetch(BASE+'/robots.txt', robots=True))


def short_term_reason(title, include_keywords=False):
    if not KEYWORDS.search(title):
        return None
    # Weekly working days and probation duration do not establish a short contract.
    cleaned = re.sub(r'주\s*\d+\s*일|수습\s*\d+\s*(?:개월|달|주|일)', '', title)
    periods = list(PERIOD.finditer(cleaned))
    if periods:
        days = [max(int(m[1] or m[2]), int(m[2])) * {'개월':31,'달':31,'주':7,'일':1}[m[3]] for m in periods]
        if any(value > 31 for value in days):
            return None
        if all(value > 0 for value in days):
            return 'explicit_title_period_at_most_31_days_needs_review'
    return 'keyword_only_duration_needs_review' if include_keywords else None


def extract_records(html, *, include_keywords=False, fetched_at=None):
    soup = BeautifulSoup(html, 'html.parser')
    form = soup.select_one('#devSearchForm[data-value-json]')
    try:
        condition = json.loads(form['data-value-json']) if form else {}
    except (ValueError, TypeError) as exc:
        raise CollectionError('검색 조건 구조가 변경되었습니다.') from exc
    if not isinstance(condition, dict) or condition.get('local') != 'N000' or condition.get('textinclude') != '단기':
        raise CollectionError('제주/단기 검색 조건이 확인되지 않습니다.')
    main = soup.select_one('#dev-gi-list')
    if main is None:
        raise CollectionError('일반 공고 목록 영역이 없습니다.')
    rows = main.select('tr[data-gno]')
    if not rows and not re.search(r'(?:검색\s*결과|채용정보|공고)(?:가|는|이)?\s*(?:없습니다|없어요)', main.get_text(' ',strip=True)):
        raise CollectionError('공고 행 또는 정상 빈 결과를 확인할 수 없습니다.')
    records = {}
    for row in rows:
        identity = row.get('data-gno', '')
        title_node = row.select_one('.tplTit strong a[href]')
        company = row.select_one('.tplCo a.link')
        fields = [node.get_text(' ',strip=True) for node in row.select('.tplTit p.etc .cell')]
        if not identity.isdigit() or title_node is None or company is None or len(fields)<4:
            raise CollectionError('공고 카드 구조가 변경되었습니다.')
        href = urlsplit(title_node['href'])
        if not href.netloc and not href.scheme and href.path == '/Recruit/GI_Read/'+identity+'/Ext':
            continue
        if (href.netloc and href.netloc != 'www.jobkorea.co.kr') or (href.scheme and href.scheme != 'https') or href.path != '/Recruit/GI_Read/'+identity:
            raise CollectionError('공고 ID와 상세 링크가 일치하지 않습니다.')
        area = fields[2]
        if not re.match(r'^제주(?:특별자치도)?(?:\s|$)', area):
            continue
        title = title_node.get_text(' ',strip=True)
        reason = short_term_reason(title, include_keywords)
        if not reason:
            continue
        closing = row.select_one('.odd .date')
        closing_text = closing.get_text(' ',strip=True) if closing else None
        if closing_text and re.fullmatch(r'(?:모집\s*|공고\s*)?마감(?:됨|되었습니다|되었어요)?',closing_text):
            continue
        posted = row.select_one('.odd .time')
        pay = next((value for value in fields[4:] if re.match(r'^(시급|일급|월급|주급|연봉|건당)\s',value)),None)
        pay_type = pay.split()[0] if pay else None
        records[identity] = {
            'source':'jobkorea','source_id':identity,'source_url':BASE+href.path,
            'title':title,'company_name':company.get_text(' ',strip=True),
            'area':area,'address':None,'employment_type_text':fields[3],
            'location_verification_status':'LISTING_REGION_ONLY_NEEDS_REVIEW',
            'working_period_text':None,'working_time_text':None,'working_week_text':None,'working_date_text':None,
            'pay_type':pay_type,'pay_text':pay[len(pay_type):].strip() if pay else None,
            'closing_text':closing_text,'posted_text':posted.get_text(' ',strip=True) if posted else None,
            'application_methods':[],'public_contact_phone':None,'sms_application_listed':False,
            'short_term_reason':reason,'collected_at':fetched_at or datetime.now(timezone.utc).isoformat(),
            'review_status':'COLLECTED','permission_status':'UNKNOWN','contact_status':'NOT_CONTACTED','publishable':False,
        }
    return list(records.values()), {'scanned':len(rows),'scope':'initial_public_general_listing_only'}


def main(argv=None):
    cli = argparse.ArgumentParser(description='잡코리아 제주/단기 공개 첫 일반 목록 후보 수집')
    cli.add_argument('--delay',type=float,default=3.0)
    cli.add_argument('--output',type=Path,default=Path('data/jobkorea-candidates.jsonl'))
    cli.add_argument('--include-keyword-candidates',action='store_true')
    cli.add_argument('--html',type=Path,help='저장한 제주/단기 목록 HTML 오프라인 파싱')
    args = cli.parse_args(argv)
    if not math.isfinite(args.delay) or args.delay<3:
        cli.error('delay는 3초 이상이어야 합니다.')
    try:
        if args.html:
            html = args.html.read_text(encoding='utf-8')
        else:
            client = JobkoreaClient(args.delay)
            client.load_robots()
            html = client.fetch(listing_url())
        records, summary = extract_records(html, include_keywords=args.include_keyword_candidates)
        write_jsonl(args.output, records)
    except (CollectionError, OSError) as exc:
        print(f'수집 실패: {exc}',file=sys.stderr)
        return 1
    print(json.dumps({'output':str(args.output),'candidates':len(records),'search':summary},ensure_ascii=False))
    return 0
