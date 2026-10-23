"""Collect only server-rendered public Daangn Jobs cards. Never call /api/."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, unquote
from urllib.request import Request, build_opener

from bs4 import BeautifulSoup
from .albamon import CollectionError, USER_AGENT, SHORT_WORDS, write_jsonl
from .alba import RobotsPolicy, NoRedirect

BASE = "https://jobs.daangn.com"
DEFAULT_REGION = "6984"  # Published Jeju-si Donam-dong search URL; not all of Jeju.


def listing_url(region_id):
    if not re.fullmatch(r"[1-9]\d*", region_id):
        raise CollectionError("region-id는 양의 정수여야 합니다.")
    return BASE + "/s?" + urlencode({"regionId": region_id})


class DaangnClient:
    def __init__(self, delay=3.0):
        self.delay, self.last, self.policy = delay, 0.0, None

    def fetch(self, url, *, robots=False):
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc != "jobs.daangn.com":
            raise CollectionError("당근알바 공개 도메인만 허용합니다.")
        # Narrower than robots.txt: only the exact search page and policy file.
        if parts.path not in {"/s", "/robots.txt"}:
            raise CollectionError("검색 페이지 이외 경로는 요청하지 않습니다.")
        if not robots and (not self.policy or not self.policy.allowed(url)):
            raise CollectionError("robots.txt에 의해 요청을 중단했습니다.")
        wait = self.delay - (time.monotonic() - self.last)
        if wait > 0: time.sleep(wait)
        self.last = time.monotonic()
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept":"text/html,text/plain"})
            with build_opener(NoRedirect).open(request, timeout=30) as response:
                data = response.read(8_000_001)
                if len(data) > 8_000_000: raise CollectionError("응답 크기 제한 초과")
                return data.decode(response.headers.get_content_charset() or "utf-8")
        except (HTTPError, URLError, UnicodeError, LookupError) as exc:
            raise CollectionError(f"요청 실패, 재시도하지 않음: {exc}") from exc

    def load_robots(self):
        self.policy = RobotsPolicy(self.fetch(BASE + "/robots.txt", robots=True))


def short_term_reason(schedule, title, include_keywords=False):
    total = re.search(r"총\s*(\d+)일", schedule)
    if total:
        if 1 <= int(total[1]) <= 31: return "explicit_total_workdays_at_most_31"
        return None
    # A weekly recurrence is not a short-term contract.
    if re.search(r"(?:월|화|수|목|금|토|일)\s*(?:~|,|·)\s*(?:월|화|수|목|금|토|일)|매주|주말|평일|월~일", schedule):
        return "keyword_only_duration_needs_review" if include_keywords and SHORT_WORDS.search(title) else None
    if re.match(r"^(오늘|내일|모레)(?:\(|\s|·|$)", schedule): return "explicit_relative_workdate_needs_review"
    if re.match(r"^\d{1,2}월\s*\d{1,2}일(?:\(|\s*·)", schedule): return "explicit_calendar_workdate_needs_review"
    if include_keywords and SHORT_WORDS.search(title): return "keyword_only_duration_needs_review"
    return None


def extract_records(html, *, region_id=DEFAULT_REGION, include_keywords=False, fetched_at=None):
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.title.get_text(strip=True) if soup.title else ""
    if not heading.startswith("제주특별자치도") or "알바" not in heading:
        raise CollectionError("제주 지역 검색 페이지인지 확인되지 않습니다. region-id/로그인/구조 변경을 확인하세요.")
    main = soup.find("main")
    if main is None: raise CollectionError("검색 결과 main 영역이 없습니다.")
    cards = [a for a in main.select('a[href^="/job-posts/"]') if a.find("h3")]
    if not cards:
        if any(text in main.get_text(" ",strip=True) for text in ["검색 결과가 없어요", "검색 결과가 없습니다", "조건에 맞는 공고가 없어요"]):
            return [], {"search_context":heading,"scanned":0,"scope":"initial_public_html_only"}
        raise CollectionError("공고 카드 또는 정상 빈 결과를 확인할 수 없습니다.")
    records, seen = [], set()
    for card in cards:
        href = urlsplit(card["href"])
        slug = unquote(href.path).removeprefix("/job-posts/")
        identity = re.search(r"-([a-z0-9]{12})$", slug)
        if not identity: raise CollectionError("공고 URL ID 형식이 변경되었습니다.")
        source_id = identity[1]
        if source_id in seen: continue
        seen.add(source_id)
        title = card.h3.get_text(" ",strip=True)
        if any(word in card.get_text(" ",strip=True) for word in ["마감되었어요", "모집 마감", "공고 마감"]): continue
        # Public seed-text spans contain wage and the displayed work schedule.
        fields = [node.get_text(" ",strip=True) for node in card.select('span.seed-text')]
        pay = next((value for value in fields if re.match(r"^(시급|일급|월급|주급|연봉|건당|건별)\s",value)),None)
        schedule = next((value for value in fields if "·" in value and value != pay),None)
        if not pay or not schedule: raise CollectionError("급여/근무 일정 카드 구조가 변경되었습니다.")
        reason = short_term_reason(schedule, title, include_keywords)
        if not reason: continue
        tags = [tag.get_text(" ",strip=True) for tag in card.select('span.seed-tag-group-item__label')]
        if not tags: raise CollectionError("공고의 지역 표시가 누락되었습니다.")
        locality = tags[-1]
        company = " · ".join(tags[:-1]) or None
        date_text, _, time_text = schedule.partition("·")
        records.append({
            "source":"daangn", "source_id":source_id,"source_url":BASE+href.path,
            "title":title,"company_name":company,"area":locality,"address":None,
            "search_region_id":region_id,"search_region_text":heading,
            "location_verification_status":"LOCALITY_ONLY_NEEDS_REVIEW",
            "working_period_text":date_text.strip(),"working_date_text":date_text.strip(),
            "working_time_text":time_text.strip(),"working_week_text":None,
            "pay_type":pay.split()[0],"pay_text":pay[len(pay.split()[0]):].strip(),
            "closing_text":None,"posted_text":None,"application_methods":[],
            "public_contact_phone":None,"sms_application_listed":False,
            "short_term_reason":reason,"collected_at":fetched_at or datetime.now(timezone.utc).isoformat(),
            "review_status":"COLLECTED","permission_status":"UNKNOWN",
            "contact_status":"NOT_CONTACTED","publishable":False,
        })
    return records,{"search_context":heading,"scanned":len(cards),"scope":"initial_public_html_only"}


def main(argv=None):
    cli=argparse.ArgumentParser(description="당근알바 제주 동네 검색의 공개 첫 목록 후보 수집")
    cli.add_argument("--region-id",action="append",help="확인한 제주 동네 regionId. 여러 번 지정 가능 (최대 5개). 기본 도남동 6984")
    cli.add_argument("--delay",type=float,default=3.0)
    cli.add_argument("--output",type=Path,default=Path("data/daangn-candidates.jsonl"))
    cli.add_argument("--include-keyword-candidates",action="store_true")
    cli.add_argument("--html",type=Path,help="저장한 제주 검색 HTML을 오프라인 파싱")
    args=cli.parse_args(argv)
    regions=list(dict.fromkeys(args.region_id or [DEFAULT_REGION]))
    if args.delay<3 or len(regions)>5: cli.error("delay 3 이상, region-id 최대 5개")
    if args.html and len(regions)>1: cli.error("오프라인 HTML에는 region-id 1개만 지정하세요.")
    records,summaries={},[]
    try:
        for region in regions: listing_url(region)
        client=None
        if not args.html: client=DaangnClient(args.delay);client.load_robots()
        for region in regions:
            html=args.html.read_text(encoding="utf-8") if args.html else client.fetch(listing_url(region))
            batch,summary=extract_records(html,region_id=region,include_keywords=args.include_keyword_candidates)
            summaries.append(summary)
            records.update((record["source_id"],record) for record in batch)
        write_jsonl(args.output,list(records.values()))
    except (CollectionError,OSError) as exc:
        print(f"수집 실패: {exc}",file=sys.stderr);return 1
    print(json.dumps({"output":str(args.output),"candidates":len(records),"searches":summaries},ensure_ascii=False))
    return 0
