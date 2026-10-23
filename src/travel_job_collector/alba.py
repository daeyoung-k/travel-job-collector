"""Alba.co.kr public short-term listing candidates (Jeju)."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, parse_qs, unquote
from urllib.request import Request, HTTPRedirectHandler, build_opener

from bs4 import BeautifulSoup
from .albamon import CollectionError, USER_AGENT, write_jsonl

BASE = "https://www.alba.co.kr"


def listing_url(page=1):
    return BASE + "/job/period/Short?" + urlencode({
        "sidocd": "064", "strAreaMulti": "064||전체||", "page": page, "pagesize": 50,
    })


class RobotsPolicy:
    """Honor longest matching Allow/Disallow rule, including * and trailing $."""
    def __init__(self, text):
        # Some responses serialize line breaks as literal backslash escapes.
        text = text.replace("\\r\\n", "\n").replace("\\n", "\n")
        groups = []
        agents, rules = [], []
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if rules:
                    groups.append((agents, rules)); agents, rules = [], []
                agents.append(value.lower())
            elif key in {"allow", "disallow"} and agents and value:
                rules.append((key == "allow", value))
        if agents:
            groups.append((agents, rules))
        if not groups:
            raise CollectionError("robots.txt의 User-agent 규칙을 확인할 수 없습니다.")
        specific = [g for g in groups if any(a != "*" and a in USER_AGENT.lower() for a in g[0])]
        selected = specific or [g for g in groups if "*" in g[0]]
        self.rules = [rule for _, rules in selected for rule in rules]

    def allowed(self, url):
        parts = urlsplit(url)
        target = parts.path + ("?" + parts.query if parts.query else "")
        matches = []
        for allow, pattern in self.rules:
            anchored = pattern.endswith("$")
            body = pattern[:-1] if anchored else pattern
            regex = "^" + re.escape(body).replace(r"\*", ".*") + ("$" if anchored else "")
            if re.search(regex, target):
                matches.append((len(body.replace("*", "")), allow))
        return max(matches)[1] if matches else True


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AlbaClient:
    def __init__(self, delay=3.0):
        self.delay = delay
        self.last = 0.0
        self.policy = None

    def fetch(self, url, *, robots=False):
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc != "www.alba.co.kr":
            raise CollectionError("알바천국 공개 HTTPS 도메인만 허용합니다.")
        if not robots and (self.policy is None or not self.policy.allowed(url)):
            raise CollectionError("robots.txt에 의해 수집을 중단했습니다.")
        wait = self.delay - (time.monotonic() - self.last)
        if wait > 0: time.sleep(wait)
        self.last = time.monotonic()
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain"})
            with build_opener(NoRedirect).open(request, timeout=30) as response:
                data = response.read(8_000_001)
                if len(data) > 8_000_000: raise CollectionError("응답 크기 제한 초과")
                charset = response.headers.get_content_charset() or "utf-8"
                return data.decode(charset)
        except (HTTPError, URLError, UnicodeError, LookupError) as exc:
            raise CollectionError(f"요청 실패, 재시도하지 않음: {exc}") from exc

    def load_robots(self):
        self.policy = RobotsPolicy(self.fetch(BASE + "/robots.txt", robots=True))


def extract_records(html: str, *, fetched_at=None):
    soup = BeautifulSoup(html, "html.parser")
    section = soup.select_one("#NormalInfo")
    if section is None: raise CollectionError("일반 목록이 없습니다. 차단 또는 구조 변경을 확인하세요.")
    paging = re.search(r"getWnSearchPaging\(\s*['\"](\d+)['\"]\s*,\s*['\"]([\d,]+)['\"]\s*,\s*(\d+)", str(section))
    if not paging: raise CollectionError("페이지 메타데이터를 확인하지 못했습니다.")
    page, total, size = int(paging[1]), int(paging[2].replace(",", "")), int(paging[3])
    if page < 1 or size < 1: raise CollectionError("유효하지 않은 페이지 정보입니다.")
    rows = section.select("tr[data-imid]")
    if total > 0 and not rows: raise CollectionError("공고 수는 있지만 공고 항목을 읽지 못했습니다.")
    records, seen, ids = [], set(), []
    for row in rows:
        source_id = row.get("data-imid", "")
        if not re.fullmatch(r"[1-9]\d*", source_id): raise CollectionError("유효하지 않은 공고 ID")
        if source_id in seen: continue
        seen.add(source_id); ids.append(source_id)
        def text(selector):
            element = row.select_one(selector)
            return element.get_text(" ", strip=True) if element else ""
        title, company, area = text("td.title span.title"), text("td.title .company"), text("td.local")
        if not title or not company or not area: raise CollectionError("공고 필수 항목 구조가 변경되었습니다.")
        # Never infer Jeju solely from the requested URL; verify each row.
        if not re.match(r"^(제주(?:특별자치도)?\s|제주시(?:\s|$)|서귀포시(?:\s|$))", area): continue
        link = row.select_one("td.title a.info")
        if not link: raise CollectionError("상세 링크 누락")
        href = urlsplit(link.get("href", ""))
        if href.netloc and href.netloc != "www.alba.co.kr": raise CollectionError("외부 상세 링크")
        if href.path.lower() != "/job/detail" or parse_qs(href.query).get("adid") != [source_id]:
            raise CollectionError("공고 ID와 상세 링크가 다릅니다.")
        posted_node = row.select_one("td.regDate")
        posted = ""
        if posted_node:
            # Exclude action-link labels such as 요약보기.
            for action in posted_node.select("a"): action.decompose()
            posted = posted_node.get_text(" ", strip=True)
        pay_type, pay_number = text("td.pay .payIcon"), text("td.pay .number")
        records.append({
            "source": "alba", "source_id": source_id,
            "source_url": BASE + "/job/Detail?" + urlencode({"adid": source_id}),
            "title": title, "company_name": company, "area": area, "address": None,
            "working_period_text": None, "working_time_text": text("td.data"),
            "working_week_text": None, "working_date_text": None,
            "pay_type": pay_type, "pay_text": pay_number + "원" if pay_number else text("td.pay"),
            "closing_text": None, "posted_text": posted,
            "application_methods": [], "public_contact_phone": None,
            "sms_application_listed": False,
            "short_term_reason": "source_short_term_category_duration_needs_review",
            "collected_at": fetched_at or datetime.now(timezone.utc).isoformat(),
            "review_status": "COLLECTED", "permission_status": "UNKNOWN",
            "contact_status": "NOT_CONTACTED", "publishable": False,
        })
    return records, {"page": page, "total_count": total, "page_size": size, "scanned": len(rows), "ids": ids}


def main(argv=None):
    cli = argparse.ArgumentParser(description="알바천국 제주 단기 메뉴의 일반 공고 후보 수집")
    cli.add_argument("--pages", type=int, default=1)
    cli.add_argument("--delay", type=float, default=3.0)
    cli.add_argument("--output", type=Path, default=Path("data/alba-candidates.jsonl"))
    cli.add_argument("--html", type=Path, help="저장한 제주 단기 목록 HTML을 오프라인 파싱")
    args = cli.parse_args(argv)
    if not 1 <= args.pages <= 10 or args.delay < 3: cli.error("pages 1~10, delay 3 이상")
    records, summaries, visited = {}, [], set()
    try:
        client = None
        if not args.html:
            client = AlbaClient(args.delay); client.load_robots()
        for page in range(1, (1 if args.html else args.pages) + 1):
            html = args.html.read_text(encoding="utf-8") if args.html else client.fetch(listing_url(page))
            batch, summary = extract_records(html)
            if not args.html and summary["page"] != page: raise CollectionError("요청/응답 페이지 불일치")
            signature = tuple(summary.pop("ids"))
            if signature and signature in visited: raise CollectionError("같은 목록이 반복되어 중단했습니다.")
            visited.add(signature)
            summaries.append(summary)
            records.update((row["source_id"], row) for row in batch)
            if page * summary["page_size"] >= summary["total_count"]: break
        write_jsonl(args.output, list(records.values()))
    except (CollectionError, OSError) as exc:
        print(f"수집 실패: {exc}", file=sys.stderr); return 1
    print(json.dumps({"output":str(args.output),"candidates":len(records),"pages":summaries},ensure_ascii=False))
    return 0
