# Travel Job Collector

알바몬 제주 공개 목록에서 단기 공고 후보를 추출하는 수집기. 웹 게시/DB 저장/문자 발송은 하지 않는다.

## 실행

Python 3.13 이상과 uv를 사용한다.

```sh
uv sync
uv run travel-job-collector
# 같은 실행의 별도 명령
uv run albamon --pages 3 --output data/albamon-candidates.jsonl
```

기본은 제주 지역 일반 목록 1페이지, 최대 10페이지, 요청 간격 최소 3초다. 광고 영역은 제외한다. 공고 ID로 실행 내 중복을 제거한다. 같은 output 파일은 성공한 실행 결과로 덮어쓰며, DB upsert나 누적 이력은 아직 없다. 조회 실패 시 기존 결과는 보존한다.

기본 단기 기준은 근무기간에 명시된 하루/1일/1주일 이하/1주일/1주일~1개월/1개월 이하/1개월이다. 협의/기타 표현은 자동 판정하지 않는다. 키워드 후보도 검토하려면:

```sh
uv run albamon --pages 3 --include-keyword-candidates
```

이때 제목에 단기/하루/당일/일일/행사/축제/일용이 있는 공고는 기간이 길거나 불확실해도 별도 사유(`keyword_only_duration_needs_review`)와 함께 포함된다. 근무 날짜·시간의 확정이나 여행자 지원 가능을 의미하지 않는다.

## 결과

한 줄당 JSON 하나인 JSONL 파일을 만든다. UTF-8 한글 유지. 기본 `data/albamon-candidates.jsonl`.

주요 필드: source/source_id/source_url, title/company_name, area/address, working_period_text/working_time_text/working_week_text/working_date_text, pay_type/pay_text, closing_text/posted_text, application_methods/public_contact_phone/sms_application_listed, short_term_reason/collected_at.

모든 항목은 `review_status=COLLECTED`, `permission_status=UNKNOWN`, `contact_status=NOT_CONTACTED`, `publishable=false`로 저장된다. 원문 급여/기간/마감은 문자열로 보존하며 확정 날짜나 순수입으로 추정 변환하지 않는다. 연락처는 공개 목록에 있는 값만 수집하며 로그인/숨겨진 API/상세 연락처 페이지를 요청하지 않는다. 연락처가 없으면 null. `sms_application_listed`는 문자 지원 방법이 기재되어 있다는 뜻이지 게시 허락 요청이나 대량 문자 수신 동의가 아니다.

결과에 연락처가 포함될 수 있어 파일을 소유자만 읽기/쓰기 가능하게 생성하고 data 디렉토리는 Git에서 제외한다. 실서비스 공개 자료로 바로 사용하지 않는다.

## 접근 범위

시작 시 robots.txt를 조회하고 공개 목록(`/jobs/area?areas=N000&page=N`)에 대해 정책을 확인한다. 명시한 TravelJobCollector User-Agent를 사용한다. 로그인·CAPTCHA 우회·차단 회피·프록시 회전·문자 발송 기능은 없다. HTTP 오류/리다이렉트/구조 변경 시 재시도하지 않고 종료한다. robots 정책 통과가 재게시 권한이나 사이트 이용약관 허용을 보장하지 않으므로 실제 운영 전 별도 확인한다.

현재 공개 HTML의 `__NEXT_DATA__` 안 `base.normal.collection` 구조를 확인해 작성했다. 페이지 구조/필드가 바뀌면 파서 수정이 필요하다. 응답 페이지가 요청 페이지와 다르면 수집을 중단한다. 일반 공고가 비어 있으면 정상 빈 결과로 처리하되 데이터 구조 누락은 실패 처리한다.

## 저장한 HTML 검사 / 테스트

```sh
uv run albamon --html /path/to/jeju-list.html --output data/offline.jsonl
uv run python -m unittest discover -s tests -v
```

테스트 fixture는 합성 데이터다. 실제 구인자 연락처/HTML을 저장소에 포함하지 않는다.

## 다음 연결

- source+source_id 유니크 키로 수집 후보 DB upsert, 실행 이력/변경 기록.
- 연락 금지/중복 연락/승인 및 거절 이력, 수동 발송 대상 검토.
- 승인 메시지 확인 후 관리자 화면에서 게시할 공고로 전환.
- 알바몬·알바천국·당근알바·잇지제주를 각각 독립 명령으로 실행한다.


## 알바천국 (alba.co.kr)

```sh
uv sync
uv run alba --pages 2
# 저장 HTML 파싱
uv run alba --html /path/to/jeju-short.html --output data/alba-offline.jsonl
```

기본 출력은 `data/alba-candidates.jsonl`. 제주(sidocd=064) 단기 메뉴 `/job/period/Short`의 일반 채용정보만 수집한다. 기본 1페이지/페이지당 50개, 최대 10페이지, 요청 간격 최소 3초. 페이지 메타데이터를 확인하고 같은 목록 반복이나 페이지 불일치 시 중단한다. robots.txt의 최장 일치 Allow/Disallow 규칙을 확인한다. 로그인·차단 우회·문자 발송은 없다.

**알바몬과 달리 단기 메뉴 목록에는 정확한 근무기간/주소/연락처가 없다.** 기간을 추정하지 않고 `short_term_reason=source_short_term_category_duration_needs_review`, 누락 필드는 null로 저장한다. 목록에는 기간이 명시되지 않아 '한 달 이내'를 확인한 결과가 아니다. source_short_term_category는 단기 메뉴에 실렸다는 의미만 있다. 모든 후보의 publishable은 false이며 별도 검토·허락이 필요하다.

업체, 제목, 지역, 근무시간, 급여, 등록일, 공고 ID와 원문 링크를 수집한다. 광고 영역과 다른 지역은 제외한다. 상세/전화번호/지원 API에는 접근하지 않는다. 연락처가 없어 이 결과만으로 문자 발송은 불가능하며, 연락 수단은 관리자 검토 후 별도로 확인해야 한다. 페이지의 요약보기/스크랩 텍스트는 등록일에서 제거한다.


## 당근알바 (jobs.daangn.com)

```sh
uv sync
uv run daangn
uv run daangn --include-keyword-candidates
uv run daangn --region-id 6984 --output data/daangn-candidates.jsonl
uv run daangn --html /path/to/jeju-search.html
```

기본은 공개 검색으로 확인한 **제주시 도남동 regionId=6984**의 주변 공고다. 제주 전체 검색/전체 수집이 아니다. 다른 제주 지역 ID는 실제 당근알바 공개 검색 주소에서 확인한 뒤 `--region-id`를 반복해 최대 5개 지정한다. 응답 title이 제주 지역 검색인지 확인한다. 카드에 보이는 동네만 저장하며 정확한 주소/시군구를 추정하지 않는다.

**공개 첫 HTML에 표시된 카드만 수집한다.** robots.txt가 금지하는 /api/ 및 로그인·지원 경로에는 접근하지 않는다. 무한 스크롤, 앱 API, 비공개 연락처, 상세 페이지는 수집하지 않는다. 따라서 `--pages` 옵션이 없으며 전체 공고를 모았다고 해석하면 안 된다.

기본 단기 조건은 총 근무일 1~31일 또는 오늘/내일/모레/특정 월일의 단일 근무 일정 표시다. 이는 연속 고용 기간 1개월 이하를 보장하지 않는다. 반복 요일 일정은 기본 제외한다. 제목 키워드만으로 판단할 공고는 `--include-keyword-candidates`로 명시적 선택한다. 상대 날짜와 연도 없는 일자는 원문 문자열 그대로 보존하며 실제 모집 유효성은 검토해야 한다.

카드에 업체가 없으면 company_name=null이고 연락처·지원방법은 비어 있다. 앱 내 지원을 문자 수신 동의로 간주하지 않는다. 후보는 항상 미승인/미연락/게시불가 상태로 저장한다. 이 결과만으로 일괄 문자 발송을 할 수 없다.

기본 결과: `data/daangn-candidates.jsonl`. 같은 공고가 여러 동네에 나오면 공고 ID로 중복 제거하며 마지막 검색 문맥이 남는다. 실패 시 기존 파일은 보존한다.


## 잇지제주 (www.idge.co.kr)

```sh
uv sync
uv run idge --pages 2
# 선택: 본문과 공개 연락 수단 확인 (기본 최대 5건)
uv run idge --details --max-details 3
# 저장한 목록 HTML 파싱
uv run idge --html /path/to/parttime.html
```

공개 아르바이트 게시판 `/parttime/`에서 `단기 1~7일`, `단기 2주 이내`, `단기 1개월 이내/한달 이내` 분류만 수집한다. 작성자를 회사명으로 추정하지 않고 source_author_name으로 별도 저장한다. 제목의 기간/급여/지역을 임의로 확정하지 않는다. 게시판 기준 제주 후보이며 실제 근무 장소/여행자 지원 가능 여부는 확인이 필요하다.

기본은 목록만 수집하고 연락처/본문은 null이다. `--details` 사용 시 공개 `.board_txt_area` 본문만 읽어 body_text와 표시된 전화번호/이메일을 추출한다. 번호 노출이 문자 동의를 의미하지 않는다. 문자 금지 문구를 발견하면 sms_contact_policy=PROHIBITED로 표시하지만, 문구 자동 판별은 완전하지 않으며 나머지는 UNKNOWN이다. 이 값만으로 자동 문자 발송 결정을 내려서는 안 된다. 신청서/사진/첨부파일/댓글/로그인 정보를 요청하지 않는다.

기본 출력은 `data/idge-candidates.jsonl`. 페이지 최대 10개, 상세 요청 최대 10개. **요청 간격 최소 10초**이며 robots.txt에 더 긴 지연이 있으면 반영한다. 목록에 실제 표시된 다음 페이지 링크만 따라가며 반복/구조 변경/리다이렉트/HTTP 오류는 중단한다. 첫 공고에 고정 공지가 반복돼도 ID 기준 중복 제거한다. 실패하면 기존 결과 파일은 유지한다.

## 급구 (www.gubgoo.com)

```sh
uv sync
uv run gubgoo --pages 2
uv run gubgoo --html /path/to/jeju-work.html
# 종료된 근무 일정도 검토할 때만
uv run gubgoo --include-ended
```

공개 `/work?region1=제주&page=1` HTML에 포함된 RSC 목록 데이터를 JSON으로 파싱한다. JavaScript 실행, 로그인, 앱 API 호출 없이 공개 페이지와 robots.txt만 요청한다. 페이지 최대 10개, 요청 간격 최소 3초. 서버가 알려준 총 페이지까지만 조회하고 ID로 중복 제거한다. 실제 공고 주소도 제주인지 검증하며 다른 지역 응답/구조 변경/HTTP 오류에는 중단하고 기존 출력 파일을 보존한다.

출력: `data/gubgoo-candidates.jsonl`. 제목, 업체, 공개 주소, 본문, 급여 단위/원본 급여, 휴게 정보, 근무 시작/종료 시각(+09:00)을 저장한다. 31일 이하로 표시된 **근무 일정** 후보이며 전체 고용 계약 기간이 단기임을 보장하지 않는다. 종료된 일정은 기본 제외한다. 연락처와 지원 방법은 이 목록에서 확인되지 않아 비워둔다. 모든 후보는 미승인/미연락/게시불가 상태다.

2026-10-22 실제 제주 공개 목록 확인 결과: 정상 빈 목록(0건, 총 페이지 0). 수집 실패와 정상 빈 결과를 구분한다. 공고가 생기면 같은 명령으로 수집할 수 있다. 빈 목록 응답은 정상 결과로 취급하므로 기존 출력 파일을 빈 파일로 교체한다.

## 잡코리아 (www.jobkorea.co.kr)

```sh
uv sync
uv run jobkorea
# 기간이 명시되지 않은 단기 키워드 후보도 검토용으로 포함
uv run jobkorea --include-keyword-candidates
uv run jobkorea --html /path/to/jeju-short-list.html --include-keyword-candidates
```

공개 지역별 목록 `/recruit/joblist?local=N000&menucode=local&textinclude=단기`의 첫 일반 공고 표만 수집한다. 검색 조건 JSON이 제주(N000)/단기인지 확인하고 각 카드 지역도 제주인지 검증한다. 추천/광고 영역, 외부 연동 `/Ext` 공고, 상세/로그인/지원 경로는 수집하지 않는다. 추가 페이지와 헤드헌팅 목록은 지원하지 않으므로 전체 공고 수집 결과가 아니다. 요청 간격 최소 3초, robots.txt 확인 후 허용된 공개 목록만 요청한다.

기본은 제목에 단기 관련 표현과 1~31일 범위 기간이 함께 있는 후보만 저장한다. `--include-keyword-candidates`는 기간 미확인 후보를 추가한다. 제목의 2개월 이상 기간, 반복 근무 요일, 수습기간을 단기 계약으로 간주하지 않는다. 단기채권 같은 무관한 표현은 제외한다. 본문에만 단기가 있는 검색 결과는 제목 기준에서 제외될 수 있다. 기간 판별은 후보 분류용이며 실제 계약 조건을 확정하지 않는다.

출력: `data/jobkorea-candidates.jsonl`. 업체/제목/카드 지역/급여/게시·마감 표시만 확인된 대로 저장한다. 상세 주소·근무시간·연락처·지원 방법은 빈 값이다. 카드의 제주 지역이 실제 근무지를 보장하지 않으며, 제목이 내륙 근무를 언급하는 공고도 있어 location_verification_status=LISTING_REGION_ONLY_NEEDS_REVIEW로 표시한다. 날짜는 표시 문자열을 보존하므로 실제 마감 여부는 재확인이 필요하다. 후보는 미승인/미연락/게시불가 상태이고 DB 저장/문자 발송/게시 기능은 없다.

2026-10-22 실제 실행: 일반 목록 14건 확인, 제목에 기간이 명시된 기본 후보 0건, 키워드 옵션 후보 6건. 실패하면 기존 파일을 보존하고 정상 0건이면 빈 파일로 교체한다.

## 사람인 (www.saramin.co.kr)

```sh
uv sync
uv run saramin
# 근무기간이 제목에 명시되지 않은 단기 키워드 후보까지 포함
uv run saramin --include-keyword-candidates
uv run saramin --html /path/to/jeju-short-list.html --include-keyword-candidates
```

공개 `/zf_user/jobs/list/domestic?loc_mcd=116000&searchword=단기`의 첫 일반 목록만 수집한다. 페이지에 포함된 공개 검색 조건 JSON의 제주(116000)/단기/첫 페이지 여부와 각 카드의 제주 지역 표시를 확인한다. 광고·추천 영역, 추가 페이지, 상세·로그인·지원 경로는 수집하지 않으므로 제주 전체 공고 수집 결과가 아니다. 요청 간격 최소 3초, 응답 크기 제한 8MB. robots.txt가 정상 정책 텍스트인지 확인한 뒤 허용된 목록만 요청하며, 정책 파일 대신 오류 HTML이 오면 중단한다.

단기 후보 기준은 잡코리아와 공유한다. 기본은 제목에 단기 관련 표현과 1~31일 범위 기간이 함께 있는 공고다. `--include-keyword-candidates`는 기간 미확인 후보를 추가한다. 2개월 이상 기간, 반복 근무 요일, 수습기간을 단기 계약으로 간주하지 않으며 단기거주시설/단기보호/단기채권 같은 표현은 제외한다. 실제 계약 조건은 별도 검토해야 한다.

출력: `data/saramin-candidates.jsonl`. 회사명(링크 유무 모두 지원), 제목, 카드 지역, 경력·고용형태 원문, 게시·마감 표시를 저장하고 ID로 중복 제거한다. 목록에서 확인하지 못한 상세 주소/급여/근무시간/연락처/지원 방법은 비워둔다. 카드 지역은 실제 근무지 보장이 아니므로 LISTING_REGION_ONLY_NEEDS_REVIEW로 표시한다. 표시 날짜는 원문을 유지하며 실제 마감 여부는 재확인이 필요하다. 모든 후보는 미승인/미연락/게시불가 상태다. DB 저장/문자 발송/게시 기능은 없다.

2026-10-22 실제 실행: 첫 일반 목록 20건 확인, 기본 후보 0건, 키워드 옵션 후보 5건. 오류에는 기존 결과 파일을 보존하고 정상 0건 결과에는 빈 파일로 교체한다.

## 잡플래닛 (www.jobplanet.co.kr) — 온라인 수집 차단 / 실제 공고 HTML 미검증

**다른 수집기처럼 실제 수집에 성공한 상태가 아니다.** 2026-10-22 동일한 수집기 User-Agent로 robots.txt는 정상 확인했으나 `/job`과 `/job/search`는 HTTP 403을 반환했다. CLI 재검증에서는 HTTP 307 리다이렉트도 확인했으며 자동으로 따라가지 않고 중단한다. 검색 파라미터가 실제로 적용되는지와 정상 공고 HTML 구조는 검증하지 못했다. 요청 성공을 보장하지 않으며 수집 건수를 0건으로 보고하지 않는다. 403에는 명확한 오류와 종료 코드 1을 반환하고 기존 결과를 보존한다. 재시도·로그인·쿠키 주입·우회 요청은 구현하지 않았다.

```sh
uv sync
# 현재 403 또는 리다이렉트(307)로 중단되는 온라인 명령
uv run jobplanet --include-keyword-candidates
# 아래는 JobPosting JSON-LD가 포함된 HTML만 지원하는 잠정 파서
uv run jobplanet --html /path/to/saved-job.html --include-keyword-candidates
```

오프라인 파서는 합성 테스트 데이터로 검증한 **잠정 표준 JSON-LD 파서**다. 실제 잡플래닛 페이지가 이 구조를 제공하는지는 미확인이다. HTML을 저장해도 구조화 데이터가 없으면 처리할 수 없으며 일반 CSS 카드나 JavaScript 전용 데이터는 지원하지 않는다. JSON-LD의 JobPosting, 배열, @graph, ItemList의 item 구조만 읽는다. 회사 리뷰·연봉 데이터는 수집하지 않는다.

구조화된 공고 주소가 잡플래닛 `/job/search?posting_ids[]=숫자`인지 확인하고, 제주 주소가 명시된 공고만 단기 필터를 적용한다. 키워드 검색 자체는 지역 근거가 아니다. 기본은 제목에 명시된 31일 이하 기간, 옵션은 기간 미확인 단기 키워드 후보다. 명시된 validThrough가 지났으면 제외하고 잘못된 날짜는 오류로 중단한다. 급여는 baseSalary 원본만 보관하며 금액/단위를 추정하지 않는다. 연락처·지원 방법은 비워두고 미승인/미연락/게시불가 상태를 유지한다.

성공 시 출력: `data/jobplanet-candidates.jsonl`. 구조화 데이터 부재/403/정책 오류에는 기존 파일을 보존한다. 구조화 공고를 확인했지만 후보 필터에서 모두 제외된 경우에는 정상 빈 결과로 출력한다. 온라인 수집 완성에는 정상 공개 응답 확보와 실제 HTML 파서 검증이 남아 있다.
