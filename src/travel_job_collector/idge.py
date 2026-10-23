"""Idge Jeju public part-time board. Manual permission review only."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, urlencode, urljoin
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError

from bs4 import BeautifulSoup
from .albamon import CollectionError, USER_AGENT, write_jsonl
from .alba import RobotsPolicy, NoRedirect

BASE = "https://www.idge.co.kr"
START = BASE + "/parttime/"


def validate_url(url, *, policy=False):
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "www.idge.co.kr":
        raise CollectionError("잇지제주 공개 도메인만 허용합니다.")
    if parts.path not in ({"/robots.txt"} if policy else {"/parttime/", "/parttime"}):
        raise CollectionError("허용된 공개 알바 게시판 경로가 아닙니다.")
    query = parse_qs(parts.query)
    if query.get("bmode", ["view"])[0] != "view":
        raise CollectionError("공개 읽기 이외 게시판 작업은 허용하지 않습니다.")
    return url


class IdgeClient:
    def __init__(self, delay=10.0):
        self.delay, self.last, self.policy = delay, 0.0, None

    def fetch(self, url, *, robots=False):
        validate_url(url, policy=robots)
        if not robots and (not self.policy or not self.policy.allowed(url)):
            raise CollectionError("robots.txt에 의해 요청을 중단했습니다.")
        wait = self.delay - (time.monotonic() - self.last)
        if wait > 0: time.sleep(wait)
        self.last = time.monotonic()
        try:
            request = Request(url, headers={"User-Agent":USER_AGENT,"Accept":"text/html,text/plain"})
            with build_opener(NoRedirect).open(request, timeout=30) as response:
                data=response.read(8_000_001)
                if len(data)>8_000_000: raise CollectionError("응답 크기 제한 초과")
                return data.decode(response.headers.get_content_charset() or "utf-8")
        except (HTTPError,URLError,UnicodeError,LookupError) as exc:
            raise CollectionError(f"요청 실패, 재시도하지 않음: {exc}") from exc

    def load_robots(self):
        text = self.fetch(BASE+"/robots.txt",robots=True)
        self.policy = RobotsPolicy(text)
        normalized=text.replace("\\r\\n","\n").replace("\\n","\n")
        # Conservatively honor the largest advertised delay, including escaped lines.
        delays = re.findall(r"(?im)^crawl-delay:\s*(\d+(?:\.\d+)?)",normalized)
        self.delay=max([self.delay,10.0]+[float(value) for value in delays])


def short_category(value):
    label=re.sub(r"[\s\[\]]","",value)
    return bool(re.fullmatch(r"단기(?:1[~～-]7일|2주이내|1개월이내|한달이내)",label))


def extract_records(html, *, fetched_at=None):
    soup=BeautifulSoup(html,"html.parser")
    cards=soup.select('a.post_link_wrap[href*="bmode=view"]')
    if not cards:
        raise CollectionError("알바 게시판 목록 카드를 확인하지 못했습니다. 빈 결과로 간주하지 않습니다.")
    now=fetched_at or datetime.now(timezone.utc).isoformat()
    records,seen,ids=[],set(),[]
    for card in cards:
        href=urljoin(BASE,card.get("href",""));validate_url(href)
        query=parse_qs(urlsplit(href).query)
        identity=query.get("idx",[""])[0]
        if not re.fullmatch(r"[1-9]\d*",identity): raise CollectionError("공고 ID 형식 변경")
        if identity in seen: continue
        seen.add(identity);ids.append(identity)
        node=card.select_one('.title-block')
        if node is None: raise CollectionError("제목 필드가 없습니다.")
        # Hidden notice and icon labels are not title/category text.
        for badge in node.select('.notice-block,.icons,.addon-badge'): badge.decompose()
        category_node=node.select_one('em')
        if category_node is None: raise CollectionError("단기 분류 필드를 확인하지 못했습니다.")
        category=category_node.get_text(' ',strip=True)
        if not short_category(category): continue
        category_node.decompose()
        title=node.get_text(' ',strip=True)
        if not title: raise CollectionError("제목이 비어 있습니다.")
        if re.search(r"모집\s*마감|구인\s*완료|채용\s*완료|모집\s*완료",title): continue
        writer=card.select_one('.writer');date=card.select_one('.date')
        records.append({
            "source":"idge","source_id":identity,
            "source_url":START+'?'+urlencode({'idx':identity,'bmode':'view'}),
            "title":title,"company_name":None,
            "source_author_name":writer.get_text(' ',strip=True) if writer else None,
            "area":"제주 (게시판 기준·근무지 확인 필요)","address":None,
            "working_period_text":category,"working_time_text":None,
            "working_week_text":None,"working_date_text":None,
            "pay_type":None,"pay_text":None,"closing_text":None,
            "posted_text":date.get('title') or date.get_text(' ',strip=True) if date else None,
            "application_methods":[],"public_contact_phone":None,"public_contact_phones":[],
            "public_contact_emails":[],"sms_application_listed":False,
            "sms_contact_policy":"UNKNOWN","body_text":None,"detail_collected":False,
            "short_term_reason":"source_explicit_short_term_category",
            "collected_at":now,"review_status":"COLLECTED",
            "permission_status":"UNKNOWN","contact_status":"NOT_CONTACTED","publishable":False,
        })
    pages={}
    for a in soup.select('a[href*="page="]'):
        target=urljoin(BASE,a.get('href',''))
        try: validate_url(target)
        except CollectionError: continue
        query=parse_qs(urlsplit(target).query)
        page=query.get('page',[''])[0]
        if page.isdigit() and 'bmode' not in query: pages[int(page)]=target
    active=soup.select_one('.pagination .active,.paging .active')
    if not active or not active.get_text(strip=True).isdigit(): raise CollectionError("현재 페이지를 확인하지 못했습니다.")
    return records,{'page':int(active.get_text(strip=True)),'scanned':len(cards),'ids':ids},pages


def enrich_detail(record,html):
    soup=BeautifulSoup(html,'html.parser')
    body=soup.select_one('.board_txt_area')
    if body is None: raise CollectionError("공개 상세 본문이 없습니다. 로그인/구조 변경을 확인하세요.")
    for hidden in body.select('script,style'): hidden.decompose()
    text=body.get_text('\n',strip=True)
    if not text: raise CollectionError("상세 본문이 비어 있습니다.")
    phones=[]
    for link in body.select('a[href^="tel:"]'):
        number=re.sub(r'[^0-9]','',link['href'][4:])
        if re.fullmatch(r'0\d{8,10}',number): phones.append(number)
    phones+= [re.sub(r'[^0-9]','',m[0]) for m in re.finditer(r'(?<!\d)(?:01[016789]|064|02)[ -]?\d{3,4}[ -]?\d{4}(?!\d)',text)]
    phones=list(dict.fromkeys(phones))
    emails=list(dict.fromkeys(re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',text)))
    prohibited=bool(re.search(r'문자\s*(?:[❌✕×Xx]|금지|불가|사절)|문자.{0,12}(?:받지|하지\s*마|사절)',text))
    result={**record,'body_text':text,'detail_collected':True,
            'public_contact_phone':phones[0] if phones else None,'public_contact_phones':phones,
            'public_contact_emails':emails,'sms_contact_policy':'PROHIBITED' if prohibited else 'UNKNOWN'}
    # Only identify channels; never infer consent to bulk messages or reposting.
    result['application_methods']=(["공개 이메일"] if emails else [])+(["공개 전화번호 (연락 방식 확인 필요)"] if phones else [])
    return result


def main(argv=None):
    cli=argparse.ArgumentParser(description='잇지제주 공개 알바 게시판의 단기 공고 후보 수집')
    cli.add_argument('--pages',type=int,default=1)
    cli.add_argument('--delay',type=float,default=10.0)
    cli.add_argument('--details',action='store_true',help='공개 상세 본문/연락처도 수집 (기본 미수집)')
    cli.add_argument('--max-details',type=int,default=5,help='상세 최대 요청 수 1~10')
    cli.add_argument('--output',type=Path,default=Path('data/idge-candidates.jsonl'))
    cli.add_argument('--html',type=Path,help='저장한 목록 HTML 오프라인 파싱')
    args=cli.parse_args(argv)
    if not 1<=args.pages<=10 or args.delay<10 or not 1<=args.max_details<=10: cli.error('pages/max-details 1~10, delay 10 이상')
    if args.html and args.details: cli.error('--html은 목록 파싱 전용이라 --details와 함께 쓸 수 없습니다.')
    records,summaries,signatures={},[],set()
    try:
        client=None
        if not args.html: client=IdgeClient(args.delay);client.load_robots()
        url=START
        for page in range(1,(1 if args.html else args.pages)+1):
            html=args.html.read_text(encoding='utf-8') if args.html else client.fetch(url)
            batch,summary,pages=extract_records(html)
            if not args.html and summary['page']!=page: raise CollectionError('요청/응답 페이지 불일치')
            signature=tuple(summary.pop('ids'))
            if signature in signatures: raise CollectionError('목록 반복으로 수집 중단')
            signatures.add(signature);summaries.append(summary)
            records.update((r['source_id'],r) for r in batch)
            if page+1 not in pages: break
            url=pages[page+1]
        if args.details:
            for identity in list(records)[:args.max_details]:
                record=records[identity]
                records[identity]=enrich_detail(record,client.fetch(record['source_url']))
        write_jsonl(args.output,list(records.values()))
    except (CollectionError,OSError) as exc:
        print(f'수집 실패: {exc}',file=sys.stderr);return 1
    print(json.dumps({'output':str(args.output),'candidates':len(records),'details':sum(r['detail_collected'] for r in records.values()),'pages':summaries},ensure_ascii=False))
    return 0
