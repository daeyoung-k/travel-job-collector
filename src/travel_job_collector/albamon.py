"""Public Albamon listing collector. No login, messaging, or publishing."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

BASE = "https://www.albamon.com"
USER_AGENT = "TravelJobCollector/0.1 (+manual-review-only)"
SHORT_WORDS = re.compile(r"단기|하루|당일|일일|행사|축제|일용")


class CollectionError(RuntimeError):
    pass


class NextDataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = False
        self.parts: list[str] = []
        self.found = False

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self.active = True
            self.found = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


def description(value):
    return value.get("description", "") if isinstance(value, dict) else str(value or "")


def short_term_reason(period: str, title: str, include_keywords: bool = False):
    """Conservative duration filter. Title-based guesses are explicitly opt-in."""
    normalized = re.sub(r"\s+", "", period)
    if normalized in {"하루(1일)", "하루", "1일", "1일이하", "1주일이하", "1주일", "1주일~1개월", "1개월이하", "1개월"}:
        return "duration_at_most_one_month"
    if include_keywords and SHORT_WORDS.search(title):
        return "keyword_only_duration_needs_review"
    return None


def extract_records(html: str, *, include_keywords=False, fetched_at=None):
    parser = NextDataParser()
    parser.feed(html)
    if not parser.found:
        raise CollectionError("__NEXT_DATA__가 없습니다. 차단/페이지 구조 변경 여부를 확인하세요.")
    try:
        props = json.loads("".join(parser.parts))["props"]["pageProps"]
        queries = props["dehydratedState"]["queries"]
    except (ValueError, KeyError, TypeError) as exc:
        raise CollectionError("목록 데이터 구조가 변경되었습니다.") from exc
    listing = None
    for query in queries:
        data = query.get("state", {}).get("data")
        if isinstance(data, dict) and isinstance(data.get("base"), dict):
            if "pagination" in data["base"] and "normal" in data["base"]:
                listing = data
                break
    if listing is None:
        raise CollectionError("공고 목록을 찾지 못했습니다. 빈 결과로 처리하지 않습니다.")
    try:
        pagination = listing["base"]["pagination"]
        page = int(pagination["page"])
        total = int(pagination["totalCount"])
        normal = listing["base"]["normal"]["collection"]
        if not isinstance(normal, list):
            raise TypeError("collection is not list")
    except (KeyError, TypeError, ValueError) as exc:
        raise CollectionError("페이지/일반 공고 구조를 확인할 수 없습니다.") from exc
    now = fetched_at or datetime.now(timezone.utc).isoformat()
    result = []
    seen = set()
    for item in normal:
        if not isinstance(item, dict):
            raise CollectionError("일반 공고 항목의 형식이 변경되었습니다.")
        source_type = item.get("recruitSourceType", {}).get("key")
        if source_type not in (None, "ALBAMON"):
            continue
        source_id = str(item.get("recruitNo", ""))
        if not source_id.isdigit() or source_id == "0":
            raise CollectionError("유효한 공고 ID가 없는 항목입니다.")
        if source_id in seen:
            continue
        seen.add(source_id)
        status = item.get("postStatusType", {}).get("key")
        if status and status != "POSTING":
            continue
        title = item.get("recruitTitle", "")
        period = item.get("workingPeriod", "")
        reason = short_term_reason(period, title, include_keywords)
        if not reason:
            continue
        area = item.get("workplaceArea", "")
        address = item.get("workplaceAddress", "")
        if not (area.startswith("제주") or address.startswith("제주")):
            continue
        methods = [description(value) for value in item.get("applicationTypes", [])]
        # Only a number already present in the public listing; no detail/contact APIs.
        phone = item.get("managerPhoneNumber") or None
        result.append({
            "source": "albamon", "source_id": source_id,
            "source_url": f"{BASE}/jobs/detail/{source_id}",
            "title": title, "company_name": item.get("companyName", ""),
            "area": area, "address": address,
            "working_period_text": period,
            "working_time_text": item.get("workingTime", ""),
            "working_week_text": item.get("workingWeek", ""),
            "working_date_text": item.get("workingDate", ""),
            "pay_type": description(item.get("payType")),
            "pay_text": item.get("pay", ""),
            "closing_text": item.get("closingDate", ""),
            "posted_text": item.get("postedDate", ""),
            "application_methods": methods, "public_contact_phone": phone,
            "sms_application_listed": any("문자" in method for method in methods),
            "short_term_reason": reason, "collected_at": now,
            "review_status": "COLLECTED", "permission_status": "UNKNOWN",
            "contact_status": "NOT_CONTACTED", "publishable": False,
        })
    return result, {"page": page, "total_count": total, "scanned": len(normal)}


class PublicClient:
    def __init__(self, delay=3.0):
        self.delay = delay
        self.last_request = 0.0
        self.robots = None

    def fetch(self, url, *, robots=False):
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc != "www.albamon.com":
            raise CollectionError("알바몬 공개 HTTPS 도메인만 허용합니다.")
        if not robots and (self.robots is None or not self.robots.can_fetch(USER_AGENT, url)):
            raise CollectionError("robots.txt 정책에 의해 요청을 중단했습니다.")
        wait = self.delay - (time.monotonic() - self.last_request)
        if wait > 0:
            time.sleep(wait)
        self.last_request = time.monotonic()
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain"})
        try:
            # No automatic redirect following: never fetch an unreviewed redirect target.
            from urllib.request import HTTPRedirectHandler, build_opener
            class NoRedirect(HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None
            with build_opener(NoRedirect).open(request, timeout=30) as response:
                raw = response.read(8_000_001)
                if len(raw) > 8_000_000:
                    raise CollectionError("응답 크기 제한을 초과했습니다.")
                return raw.decode("utf-8-sig")
        except (HTTPError, URLError, UnicodeError) as exc:
            raise CollectionError(f"요청 실패, 자동 재시도하지 않음: {exc}") from exc

    def load_robots(self):
        text = self.fetch(f"{BASE}/robots.txt", robots=True)
        if "user-agent:" not in text.lower():
            raise CollectionError("robots.txt를 확인하지 못해 중단했습니다.")
        parser = RobotFileParser()
        parser.parse(text.splitlines())
        self.robots = parser
        self.delay = max(self.delay, float(parser.crawl_delay(USER_AGENT) or 0))


def write_jsonl(path: Path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep raw contact-containing output owner-readable; replace atomically.
    import os
    import tempfile
    fd, name = tempfile.mkstemp(prefix=".collector-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            for record in records:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main(argv=None):
    cli = argparse.ArgumentParser(description="알바몬 제주 단기 공고 후보 수집 (게시/문자발송 없음)")
    cli.add_argument("--pages", type=int, default=1, help="일반 목록 페이지 수 (1~10)")
    cli.add_argument("--delay", type=float, default=3.0, help="요청 간격 초 (최소 3)")
    cli.add_argument("--output", type=Path, default=Path("data/albamon-candidates.jsonl"))
    cli.add_argument("--include-keyword-candidates", action="store_true", help="제목만 단기인 불확실 후보도 별도 표시하여 포함")
    cli.add_argument("--html", type=Path, help="네트워크 없이 저장한 제주 목록 HTML 파싱")
    args = cli.parse_args(argv)
    if not 1 <= args.pages <= 10 or args.delay < 3:
        cli.error("--pages는 1~10, --delay는 3 이상이어야 합니다.")
    records = {}
    summaries = []
    try:
        if args.html:
            batch, summary = extract_records(args.html.read_text(encoding="utf-8"), include_keywords=args.include_keyword_candidates)
            records.update((item["source_id"], item) for item in batch)
            summaries.append(summary)
        else:
            client = PublicClient(args.delay)
            client.load_robots()
            for page in range(1, args.pages + 1):
                url = f"{BASE}/jobs/area?{urlencode({'areas': 'N000', 'page': page})}"
                batch, summary = extract_records(client.fetch(url), include_keywords=args.include_keyword_candidates)
                if summary["page"] != page:
                    raise CollectionError("요청과 응답 페이지가 다릅니다. 페이지 구조를 확인하세요.")
                summaries.append(summary)
                records.update((item["source_id"], item) for item in batch)
                if summary["scanned"] == 0 or page * 20 >= summary["total_count"]:
                    break
        write_jsonl(args.output, list(records.values()))
    except (CollectionError, OSError) as exc:
        print(f"수집 실패: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"output": str(args.output), "candidates": len(records), "pages": summaries}, ensure_ascii=False))
    return 0
