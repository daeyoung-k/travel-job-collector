"""Read public Saramin Jeju short-work search cards without login."""
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
from urllib.parse import urlencode, urlsplit, parse_qs
from urllib.request import Request, build_opener
from bs4 import BeautifulSoup
from .alba import NoRedirect, RobotsPolicy
from .albamon import CollectionError, USER_AGENT, write_jsonl
from .jobkorea import short_term_reason

BASE = 'https://www.saramin.co.kr'


def listing_url():
    return BASE+'/zf_user/jobs/list/domestic?'+urlencode({'loc_mcd':'116000','searchword':'단기'})


class SaraminClient:
    def __init__(self, delay=3.0):
        self.delay, self.last, self.policy = delay, 0.0, None

    def fetch(self, url, *, robots=False):
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.netloc != 'www.saramin.co.kr' or parts.path not in {'/zf_user/jobs/list/domestic','/robots.txt'}:
            raise CollectionError('사람인 공개 목록 경로만 허용합니다.')
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
        text = self.fetch(BASE+'/robots.txt', robots=True)
        if '<' in text or not re.search(r'^\s*User-agent\s*:', text, re.I | re.M):
            raise CollectionError('robots.txt가 정상 정책 파일이 아닙니다. 수집을 중단합니다.')
        self.policy = RobotsPolicy(text)



def search_condition(soup):
    # Public config contains a JSON string nested in JavaScript; decode JSON only.
    decoder = json.JSONDecoder()
    for script in soup.find_all('script'):
        text = script.string or ''
        for marker in re.finditer(r"'params'\s*:\s*(?=\")", text):
            try:
                encoded, _ = decoder.raw_decode(text[marker.end():])
                value = json.loads(encoded)
            except (ValueError, TypeError) as exc:
                raise CollectionError('사람인 검색 조건 JSON이 변경되었습니다.') from exc
            if isinstance(value, dict) and value.get('action') == 'domestic':
                return value
    raise CollectionError('사람인 공개 검색 조건을 찾을 수 없습니다.')


def extract_records(html, *, include_keywords=False, fetched_at=None):
    soup = BeautifulSoup(html, 'html.parser')
    condition = search_condition(soup)
    if condition.get('loc_mcd') != '116000' or condition.get('searchword') != '단기' or condition.get('page') != 1:
        raise CollectionError('제주/단기 첫 페이지 검색 조건이 확인되지 않습니다.')
    main = soup.select_one('#default_list_wrap .list_body')
    if main is None:
        raise CollectionError('사람인 일반 공고 영역이 없습니다.')
    cards = main.select('.list_item[id^="rec-"]')
    if not cards and not re.search(r'(?:검색\s*결과|채용정보|공고)(?:가|는|이)?\s*(?:없습니다|없어요)', main.get_text(' ',strip=True)):
        raise CollectionError('일반 공고 또는 정상 빈 결과를 확인할 수 없습니다.')
    records = {}
    for card in cards:
        identity = card['id'].removeprefix('rec-')
        title_node = card.select_one('.job_tit a[href]')
        company = card.select_one('.company_nm .str_tit')
        area_node = card.select_one('.work_place')
        if not identity.isdigit() or title_node is None or company is None or area_node is None:
            raise CollectionError('사람인 공고 카드 구조가 변경되었습니다.')
        href = urlsplit(title_node['href'])
        if (href.netloc and href.netloc != 'www.saramin.co.kr') or (href.scheme and href.scheme != 'https') or href.path != '/zf_user/jobs/relay/view' or parse_qs(href.query).get('rec_idx') != [identity]:
            raise CollectionError('공고 링크와 ID가 일치하지 않습니다.')
        area = area_node.get_text(' ',strip=True)
        if not re.match(r'^제주(?:특별자치도)?(?:\s|$)',area):
            continue
        title = title_node.get_text(' ',strip=True)
        # These describe facilities/financial products, not employment duration.
        if re.search(r'단기\s*(?:거주|보호|채권|금융|대출)',title):
            continue
        reason = short_term_reason(title,include_keywords)
        if not reason:
            continue
        closing = card.select_one('.support_detail .date')
        closing_text = closing.get_text(' ',strip=True) if closing else None
        if closing_text and re.fullmatch(r'(?:모집\s*|공고\s*)?마감(?:됨|되었습니다|되었어요)?',closing_text):
            continue
        posted = card.select_one('.support_detail .deadlines')
        career = card.select_one('.career')
        records[identity] = {
            'source':'saramin','source_id':identity,'source_url':BASE+'/zf_user/jobs/view?'+urlencode({'rec_idx':identity}),
            'title':title,'company_name':company.get_text(' ',strip=True),'area':area,'address':None,
            'career_and_employment_text':career.get_text(' ',strip=True) if career else None,
            'location_verification_status':'LISTING_REGION_ONLY_NEEDS_REVIEW',
            'working_period_text':None,'working_date_text':None,'working_time_text':None,'working_week_text':None,
            'pay_type':None,'pay_text':None,'closing_text':closing_text,
            'posted_text':posted.get_text(' ',strip=True) if posted else None,
            'application_methods':[],'public_contact_phone':None,'sms_application_listed':False,
            'short_term_reason':reason,'collected_at':fetched_at or datetime.now(timezone.utc).isoformat(),
            'review_status':'COLLECTED','permission_status':'UNKNOWN','contact_status':'NOT_CONTACTED','publishable':False,
        }
    return list(records.values()),{'scanned':len(cards),'scope':'initial_public_general_listing_only'}


def main(argv=None):
    cli = argparse.ArgumentParser(description='사람인 제주/단기 공개 첫 일반 목록 후보 수집')
    cli.add_argument('--delay',type=float,default=3.0)
    cli.add_argument('--output',type=Path,default=Path('data/saramin-candidates.jsonl'))
    cli.add_argument('--include-keyword-candidates',action='store_true')
    cli.add_argument('--html',type=Path,help='저장한 제주/단기 목록 HTML 오프라인 파싱')
    args = cli.parse_args(argv)
    if not math.isfinite(args.delay) or args.delay<3:
        cli.error('delay는 3초 이상이어야 합니다.')
    try:
        if args.html:
            html = args.html.read_text(encoding='utf-8')
        else:
            client = SaraminClient(args.delay)
            client.load_robots()
            html = client.fetch(listing_url())
        records, summary = extract_records(html,include_keywords=args.include_keyword_candidates)
        write_jsonl(args.output,records)
    except (CollectionError,OSError) as exc:
        print(f'수집 실패: {exc}',file=sys.stderr)
        return 1
    print(json.dumps({'output':str(args.output),'candidates':len(records),'search':summary},ensure_ascii=False))
    return 0
