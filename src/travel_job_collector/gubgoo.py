"""Read public Gubgoo work-page RSC data, without executing JavaScript."""
from __future__ import annotations
import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from bs4 import BeautifulSoup
from .albamon import CollectionError, write_jsonl
from .daangn import DaangnClient
from .alba import RobotsPolicy

BASE='https://www.gubgoo.com'
KST=timezone(timedelta(hours=9))


def listing_url(page=1):
    return BASE+'/work?'+urlencode({'region1':'제주','page':page})


class GubgooClient(DaangnClient):
    def fetch(self,url,*,robots=False):
        # Reuse the bounded, throttled transport with this source's allowlist.
        parts=urlsplit(url)
        if parts.scheme!='https' or parts.netloc!='www.gubgoo.com' or parts.path not in {'/work','/robots.txt'}:
            raise CollectionError('급구 공개 목록 경로만 허용합니다.')
        from urllib.request import Request, build_opener
        from urllib.error import HTTPError, URLError
        from .alba import NoRedirect
        from .albamon import USER_AGENT
        import time
        if not robots and (not self.policy or not self.policy.allowed(url)):
            raise CollectionError('robots.txt에 의해 요청을 중단했습니다.')
        wait=self.delay-(time.monotonic()-self.last)
        if wait>0: time.sleep(wait)
        self.last=time.monotonic()
        try:
            with build_opener(NoRedirect).open(Request(url,headers={'User-Agent':USER_AGENT,'Accept':'text/html,text/plain'}),timeout=30) as response:
                data=response.read(8_000_001)
                if len(data)>8_000_000: raise CollectionError('응답 크기 제한 초과')
                return data.decode(response.headers.get_content_charset() or 'utf-8')
        except (HTTPError,URLError,UnicodeError,LookupError) as exc:
            raise CollectionError(f'요청 실패: {exc}') from exc
    def load_robots(self):
        self.policy=RobotsPolicy(self.fetch(BASE+'/robots.txt',robots=True))


def work_date(value):
    return datetime(value['Y'],value['M'],value['D'],value.get('h',0),value.get('m',0),value.get('s',0),tzinfo=KST)


def extract_records(html,*,page=1,now=None,include_ended=False):
    decoder=json.JSONDecoder(); chunks=[]
    try:
        for script in BeautifulSoup(html,'html.parser').find_all('script'):
            content=script.string or ''; marker='self.__next_f.push('; position=0
            while (position:=content.find(marker,position))>=0:
                position+=len(marker)
                value,used=decoder.raw_decode(content[position:]);position+=used
                if isinstance(value,list) and len(value)==2 and value[0]==1 and isinstance(value[1],str): chunks.append(value[1])
        payload=''.join(chunks); marker='"initialWorkListData":'
        position=payload.find(marker)
        if position<0: raise CollectionError('급구 공개 목록 데이터를 찾을 수 없습니다.')
        data,_=decoder.raw_decode(payload[position+len(marker):].lstrip())
        if not isinstance(data,dict) or not isinstance(data.get('list'),list) or data.get('currentPage')!=page:
            raise CollectionError('급구 목록 구조 또는 페이지 번호가 변경되었습니다.')
        total=data.get('totalPage')
        if type(total) is not int or total<0: raise CollectionError('총 페이지 수가 올바르지 않습니다.')
        now=now or datetime.now(timezone.utc);records={};ended=0
        for item in data['list']:
            identity=item['id'];address=item['addressInfo']
            if not re.fullmatch('[a-f0-9]{24}',identity): raise CollectionError('공고 ID 형식 변경')
            if address['region1']!='제주': raise CollectionError('제주 이외 지역이 반환되어 중단합니다.')
            start,end=work_date(item['startAt']),work_date(item['endAt'])
            if end<=start: raise CollectionError('근무 종료 시각이 시작보다 빠릅니다.')
            if end<now and not include_ended: ended+=1;continue
            if end-start>timedelta(days=31): continue
            wage=item['wage'];pay_type=wage['type']
            # Preserve displayed unit; never substitute daily totals for hourly pay.
            key={'시급':'hourly','일급':'daily','주급':'weekly','월급':'monthly'}.get(pay_type)
            amount=wage.get(key) if key else None
            if amount is not None and (isinstance(amount,bool) or not isinstance(amount,(int,float)) or not math.isfinite(amount) or amount<0): raise CollectionError('급여 값이 올바르지 않습니다.')
            records[identity]={
                'source':'gubgoo','source_id':identity,'source_url':BASE+'/work/'+identity,
                'title':item['title'],'company_name':(item.get('offer') or {}).get('name'),
                'area':' '.join(filter(None,[address.get('region1'),address.get('region2'),address.get('region3')])),
                'address':address.get('full'),'working_period_text':f'{start:%Y-%m-%d} ~ {end:%Y-%m-%d}',
                'working_date_text':f'{start:%Y-%m-%d} ~ {end:%Y-%m-%d}',
                'working_time_text':f'{start:%H:%M} ~ {end:%H:%M}', 'working_week_text':None,
                'work_start_at':start.isoformat(),'work_end_at':end.isoformat(),
                'pay_type':pay_type,'pay_text':f'{amount:,}원' if amount is not None else None,
                'source_wage':wage,'break_time':item.get('breakTime'),'body_text':item.get('content'),
                'closing_text':None,'posted_text':None,'application_methods':[],
                'public_contact_phone':None,'sms_application_listed':False,
                'short_term_reason':'listed_shift_at_most_31_days_contract_needs_review',
                'collected_at':now.isoformat(),'review_status':'COLLECTED','permission_status':'UNKNOWN',
                'contact_status':'NOT_CONTACTED','publishable':False}
        return list(records.values()),{'scanned':len(data['list']),'ended_skipped':ended,'current_page':page,'total_pages':total}
    except (KeyError,TypeError,ValueError,OverflowError,AttributeError) as exc:
        raise CollectionError(f'급구 공개 목록 구조/값을 확인하세요: {exc}') from exc


def main(argv=None):
    cli=argparse.ArgumentParser(description='급구 공개 제주 근무 일정 후보 수집')
    cli.add_argument('--pages',type=int,default=1)
    cli.add_argument('--delay',type=float,default=3.0)
    cli.add_argument('--output',type=Path,default=Path('data/gubgoo-candidates.jsonl'))
    cli.add_argument('--html',type=Path)
    cli.add_argument('--include-ended',action='store_true',help='종료된 근무 일정도 검토용으로 포함')
    args=cli.parse_args(argv)
    if not 1<=args.pages<=10 or not math.isfinite(args.delay) or args.delay<3: cli.error('pages 1~10, delay 3초 이상')
    if args.html and args.pages!=1: cli.error('오프라인 HTML은 1페이지만 지원합니다.')
    records={};summaries=[]
    try:
        client=GubgooClient(args.delay)
        if not args.html: client.load_robots()
        for page in range(1,args.pages+1):
            html=args.html.read_text(encoding='utf-8') if args.html else client.fetch(listing_url(page))
            batch,summary=extract_records(html,page=page,include_ended=args.include_ended)
            records.update((row['source_id'],row) for row in batch);summaries.append(summary)
            if page>=summary['total_pages']: break
        write_jsonl(args.output,list(records.values()))
    except (CollectionError,OSError) as exc:
        print(f'수집 실패: {exc}',file=sys.stderr);return 1
    print(json.dumps({'output':str(args.output),'candidates':len(records),'pages':summaries},ensure_ascii=False));return 0
