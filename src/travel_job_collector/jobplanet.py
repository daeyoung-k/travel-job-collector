"""Jobplanet adapter. Live access currently blocked; JSON-LD parsing is provisional."""
from __future__ import annotations
import argparse
import json
import math
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, parse_qs
from urllib.request import Request, build_opener
from bs4 import BeautifulSoup
from .alba import NoRedirect, RobotsPolicy
from .albamon import CollectionError, USER_AGENT, write_jsonl
from .jobkorea import short_term_reason

BASE = 'https://www.jobplanet.co.kr'
KST = timezone(timedelta(hours=9))


def listing_url():
    # Keyword is a search hint only; location must be verified per posting.
    return BASE+'/job/search?'+urlencode({'q':'제주 단기'})


class JobplanetClient:
    def __init__(self, delay=3.0):
        self.delay, self.last, self.policy = delay, 0.0, None

    def fetch(self, url, *, robots=False):
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.netloc != 'www.jobplanet.co.kr' or parts.path not in {'/job/search','/robots.txt'}:
            raise CollectionError('잡플래닛 공개 목록 경로만 허용합니다.')
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
        except HTTPError as exc:
            if exc.code == 403:
                raise CollectionError('HTTP 403: 잡플래닛이 자동 요청을 거부했습니다. 기존 파일을 보존하고 중단합니다.') from exc
            raise CollectionError(f'HTTP 오류: {exc.code}') from exc
        except (URLError, UnicodeError, LookupError) as exc:
            raise CollectionError(f'요청 실패, 재시도하지 않음: {exc}') from exc

    def load_robots(self):
        text = self.fetch(BASE+'/robots.txt', robots=True)
        if '<' in text or not re.search(r'^\s*User-agent\s*:', text, re.I | re.M):
            raise CollectionError('robots.txt가 정상 정책 파일이 아닙니다. 수집을 중단합니다.')
        self.policy = RobotsPolicy(text)



def walk_jobs(value):
    if isinstance(value,list):
        for entry in value:
            yield from walk_jobs(entry)
    elif isinstance(value,dict):
        types = value.get('@type',[])
        if types == 'JobPosting' or isinstance(types,list) and 'JobPosting' in types:
            yield value
        for key in ('@graph','itemListElement','item'):
            if key in value:
                yield from walk_jobs(value[key])


def source_identity(url):
    if not isinstance(url,str):
        raise CollectionError('잡플래닛 공고 URL이 없습니다.')
    parts = urlsplit(url)
    ids = parse_qs(parts.query).get('posting_ids[]',[])
    if parts.scheme != 'https' or parts.netloc != 'www.jobplanet.co.kr' or parts.path != '/job/search' or len(ids)!=1 or not ids[0].isdigit():
        raise CollectionError('검증된 잡플래닛 공고 주소 형식이 아닙니다.')
    return ids[0]


def verified_location(value):
    locations = value if isinstance(value,list) else [value]
    for location in locations:
        if not isinstance(location,dict):
            continue
        address = location.get('address')
        if not isinstance(address,dict):
            continue
        region,locality = address.get('addressRegion'),address.get('addressLocality')
        if not all(v is None or isinstance(v,str) for v in (region,locality)):
            raise CollectionError('지역 값이 올바르지 않습니다.')
        area = ' '.join(filter(None,[region,locality]))
        if re.match(r'^제주(?:특별자치도)?(?:\s|$)',area):
            street = address.get('streetAddress')
            if street is not None and not isinstance(street,str):
                raise CollectionError('주소 값이 올바르지 않습니다.')
            return area, ' '.join(filter(None,[area,street]))
    return None


def expired(value,now):
    if value is None:
        return False
    try:
        if not isinstance(value,str):
            raise ValueError('마감일이 문자열이 아닙니다.')
        end = datetime.fromisoformat(value.replace('Z','+00:00'))
        if len(value)==10:
            end = end.replace(hour=23,minute=59,second=59)
        if end.tzinfo is None:
            end = end.replace(tzinfo=KST)
        return end < now
    except ValueError as exc:
        raise CollectionError('공고 마감 날짜를 해석할 수 없습니다.') from exc


def extract_records(html,*,include_keywords=False,now=None):
    soup = BeautifulSoup(html,'html.parser')
    jobs = []
    try:
        for script in soup.select('script[type="application/ld+json"]'):
            jobs.extend(walk_jobs(json.loads(script.string or '')))
    except (ValueError,TypeError,RecursionError) as exc:
        raise CollectionError('JobPosting JSON-LD 구조를 확인하세요.') from exc
    if not jobs:
        raise CollectionError('JobPosting 구조화 데이터를 확인할 수 없습니다. 차단 응답/미지원 HTML이며 정상 0건으로 저장하지 않습니다.')
    now = now or datetime.now(timezone.utc)
    records = {}
    for job in jobs:
        title = job.get('title')
        if not isinstance(title,str) or not title.strip():
            raise CollectionError('공고 제목이 없습니다.')
        identity = source_identity(job.get('url'))
        location = verified_location(job.get('jobLocation'))
        if not location or expired(job.get('validThrough'),now):
            continue
        if re.search(r'단기\s*(?:거주|보호|채권|금융|대출)',title):
            continue
        reason = short_term_reason(title,include_keywords)
        if not reason:
            continue
        organization = job.get('hiringOrganization') or {}
        if not isinstance(organization,dict):
            raise CollectionError('채용 기업 구조를 확인하세요.')
        body = job.get('description')
        if body is not None and not isinstance(body,str):
            raise CollectionError('공고 본문 형식이 올바르지 않습니다.')
        area,address = location
        records[identity] = {
            'source':'jobplanet','source_id':identity,
            'source_url':BASE+'/job/search?'+urlencode({'posting_ids[]':identity}),
            'title':title.strip(),'company_name':organization.get('name'),'area':area,'address':address,
            'body_text':BeautifulSoup(body,'html.parser').get_text(' ',strip=True) if body else None,
            'employment_type_text':job.get('employmentType'),'source_salary':job.get('baseSalary'),
            'working_period_text':None,'working_date_text':None,'working_time_text':None,'working_week_text':None,
            'pay_type':None,'pay_text':None,'closing_text':job.get('validThrough'),'posted_text':job.get('datePosted'),
            'application_methods':[],'public_contact_phone':None,'sms_application_listed':False,
            'location_verification_status':'STRUCTURED_LOCATION_NEEDS_REVIEW',
            'short_term_reason':reason,'collected_at':now.isoformat(),'review_status':'COLLECTED',
            'permission_status':'UNKNOWN','contact_status':'NOT_CONTACTED','publishable':False,
        }
    return list(records.values()),{'scanned':len(jobs),'scope':'jobposting_jsonld_only_unverified_live_schema'}


def main(argv=None):
    cli = argparse.ArgumentParser(description='잡플래닛 어댑터 (온라인 403 확인, JSON-LD 파서는 실제 HTML 미검증)')
    cli.add_argument('--delay',type=float,default=3.0)
    cli.add_argument('--output',type=Path,default=Path('data/jobplanet-candidates.jsonl'))
    cli.add_argument('--include-keyword-candidates',action='store_true')
    cli.add_argument('--html',type=Path,help='JobPosting JSON-LD가 포함된 저장 HTML 파싱')
    args = cli.parse_args(argv)
    if not math.isfinite(args.delay) or args.delay<3:
        cli.error('delay는 3초 이상이어야 합니다.')
    try:
        if args.html:
            html = args.html.read_text(encoding='utf-8')
        else:
            client = JobplanetClient(args.delay)
            client.load_robots()
            html = client.fetch(listing_url())
        records, summary = extract_records(html,include_keywords=args.include_keyword_candidates)
        write_jsonl(args.output,records)
    except (CollectionError,OSError) as exc:
        print(f'수집 실패: {exc}',file=sys.stderr)
        return 1
    print(json.dumps({'output':str(args.output),'candidates':len(records),'search':summary},ensure_ascii=False))
    return 0
