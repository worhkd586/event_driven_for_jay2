"""data/dart_events.json → data/view.json (대시보드가 그대로 그리는 데이터).

하는 일
1. 같은 회사·같은 보고서의 정정공시를 원공시와 묶는다 (원공시가 나오면 새 묶음).
2. 묶음마다 DART 주요사항 상세 API를 조회해 상세 항목·일정을 붙인다 (data/details.json에 캐시).
3. 주주배정·일반공모 유상증자는 증권신고서(estkRs)로 발행가·청약일정·주관사를 붙인다.
4. 진행 중 판단: 일정 중 가장 늦은 날짜(신주상장·합병기일·분할기일 등)가 오늘 이후면 진행 중.
5. 진행 중 종목의 시세(현재가·전일종가·시총)를 네이버 금융에서 묶어서 가져온다.

환경변수: DART_API_KEY (필수)
"""

import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

API = "https://opendart.fss.or.kr/api"
KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "dart_events.json")
CACHE = os.path.join(ROOT, "data", "details.json")
OUT = os.path.join(ROOT, "data", "view.json")
TEXT_MAX = 700            # 긴 서술형 항목은 이 길이까지만 보관 (전문은 DART 원문)
ESTK_TTL_MIN = 60         # 진행 중 유상증자의 증권신고서 재조회 간격 (발행가 정정 반영)
FALLBACK_DAYS = 45        # 일정을 알 수 없는 이벤트는 공시일 + N일까지 진행 중으로 본다

# 보고서명 → 상세 API
DETAIL_API = [
    (r"유무상증자결정", "pifricDecsn"),
    (r"유상증자결정", "piicDecsn"),
    (r"무상증자결정", "fricDecsn"),
    (r"회사분할합병결정", "cmpDvmgDecsn"),
    (r"회사합병결정", "cmpMgDecsn"),
    (r"회사분할결정", "cmpDvDecsn"),
    (r"주식교환.?이전결정", "stkExtrDecsn"),
    (r"영업양수결정", "bsnInhDecsn"),
    (r"영업양도결정", "bsnTrfDecsn"),
    (r"신주인수권부사채권발행결정", "bdwtIsDecsn"),
]

# 상세 항목 라벨 (표시 순서대로). 없는 키는 건너뛴다.
LABELS = [
    ("ic_mthn", "증자 방식"), ("nstk_ostk_cnt", "신주 보통주 수"), ("nstk_estk_cnt", "신주 기타주식 수"),
    ("bfic_tisstk_ostk", "증자 전 발행주식(보통)"), ("fv_ps", "1주당 액면가"),
    ("fdpp_fclt", "자금용도: 시설"), ("fdpp_bsninh", "자금용도: 영업양수"), ("fdpp_op", "자금용도: 운영"),
    ("fdpp_dtrp", "자금용도: 채무상환"), ("fdpp_ocsa", "자금용도: 타법인 증권 취득"), ("fdpp_etc", "자금용도: 기타"),
    ("ssl_at", "공매도 해당 여부"), ("ssl_bgd", "공매도 기간 시작"), ("ssl_edd", "공매도 기간 종료"),
    ("nstk_ascnt_ps_ostk", "1주당 배정 신주(보통)"), ("nstk_asstd", "신주배정기준일"), ("nstk_lstprd", "신주 상장예정일"),
    ("mg_mth", "합병 방법"), ("mg_stn", "합병 형태"), ("mg_rt", "합병비율"), ("mgptncmp_cmpnm", "합병 상대회사"),
    ("mgptncmp_rl_cmpn", "상대회사와의 관계"), ("mgnstk_ostk_cnt", "합병신주(보통)"),
    ("extr_sen", "구분"), ("extr_stn", "교환 형태"), ("extr_tgcmp_cmpnm", "교환 대상회사"), ("extr_tgcmp_rl_cmpn", "대상회사와의 관계"),
    ("extr_rt", "교환비율"),
    ("dvfcmp_cmpnm", "분할신설회사"), ("dvfcmp_mbsn", "신설회사 주요사업"), ("dv_rt", "분할비율"),
    ("atdv_excmp_atdv_lstmn_atn", "분할 후 상장 유지"),
    ("inh_bsn", "양수 영업"), ("inh_prc", "양수가액"), ("trf_bsn", "양도 영업"), ("trf_prc", "양도가액"),
    ("ast_rt", "자산 대비 비중(%)"), ("sl_rt", "매출 대비 비중(%)"), ("dlptn_cmpnm", "거래 상대방"),
    ("ftc_stt_atn", "공정위 신고 대상"),
    ("aprskh_plnprc", "주식매수청구가"), ("aprskh_ctref", "매수청구 관련 계약 해제 조건"), ("aprskh_lmt", "매수청구권 제한"),
    ("exevl_intn", "외부평가기관"), ("exevl_op", "외부평가 의견"), ("popt_ctr_atn", "풋옵션 등 계약"),
    ("rs_sm_atn", "증권신고서 제출 대상"), ("ex_sm_r", "신고서 제출 면제 사유"),
]
# 일정 항목 (라벨). 이 날짜들 중 가장 늦은 날이 이벤트 종료일이 된다.
SCHEDULE = [
    ("bddd", "이사회 결의일"), ("ssl_edd", "공매도 기간 종료"), ("nstk_asstd", "신주배정기준일"), ("nstk_lstprd", "신주 상장예정일"),
    ("mgsc_mgctrd", "합병계약일"), ("mgsc_shddstd", "주주확정 기준일"), ("mgsc_mgop_rcpd_bgd", "반대의사 통지 시작"),
    ("mgsc_mgop_rcpd_edd", "반대의사 통지 종료"), ("mgsc_gmtsck_prd", "합병 주총"), ("mgsc_aprskh_expd_bgd", "매수청구 시작"),
    ("mgsc_aprskh_expd_edd", "매수청구 종료"), ("mgsc_cdobprpd_edd", "채권자 이의 종료"), ("mgsc_mgdt", "합병기일"),
    ("mgsc_mgrgsprd", "합병등기 예정일"), ("mgsc_nstklstprd", "합병신주 상장예정일"),
    ("extrsc_extrctrd", "교환계약일"), ("extrsc_shddstd", "주주확정 기준일"), ("extrsc_extrop_rcpd_bgd", "반대의사 통지 시작"),
    ("extrsc_extrop_rcpd_edd", "반대의사 통지 종료"), ("extrsc_gmtsck_prd", "교환 주총"), ("extrsc_aprskh_expd_bgd", "매수청구 시작"),
    ("extrsc_aprskh_expd_edd", "매수청구 종료"), ("extrsc_extrdt", "교환일"), ("extrsc_nstklstprd", "신주 상장예정일"),
    ("gmtsck_prd", "주주총회 예정일"), ("cdobprpd_edd", "채권자 이의 종료"), ("dvdt", "분할기일"), ("dvrgsprd", "분할등기 예정일"),
    ("abcr_nstklstprd", "분할신주 상장예정일"),
    ("inh_prd_ctr_cnsd", "계약 체결일"), ("inh_prd_inh_std", "양수 기준일"), ("trf_prd_ctr_cnsd", "계약 체결일"),
    ("trf_prd_trf_std", "양도 기준일"),
]
# 종료일 계산에서 제외 (과거 기준일이거나 너무 먼 날짜)
END_EXCLUDE = {"bddd", "inh_prd_ctr_cnsd", "trf_prd_ctr_cnsd", "mgsc_mgctrd", "extrsc_extrctrd"}


def call(path, params, key, retries=3):
    q = urllib.parse.urlencode(dict(params, crtfc_key=key))
    for i in range(retries):
        try:
            time.sleep(0.12)   # 요청 간격 (OpenDART 과다 요청 제한 회피)
            with urllib.request.urlopen(f"{API}/{path}.json?{q}", timeout=20) as r:
                d = json.load(r)
            st = d.get("status")
            if st == "020" and i < retries - 1:   # 요청 제한 초과: 잠시 쉬고 재시도
                print(f"{path}: 020 요청 제한, 재시도", file=sys.stderr)
                time.sleep(5 * (i + 1))
                continue
            if st not in ("000", "013"):
                print(f"{path} {params.get('corp_code', '')}: {st} {d.get('message')}", file=sys.stderr)
            return d
        except Exception as e:
            if i == retries - 1:
                print(f"{path}: {e}", file=sys.stderr)
                return {}
            time.sleep(2 ** i)


def to_date(v):
    """'2026년 10월 23일' / '20261023' / '2026-10-23' → '2026-10-23'. 아니면 None."""
    s = str(v or "")
    m = re.search(r"(20\d{2})\s*[년.\-/]?\s*(\d{1,2})\s*[월.\-/]?\s*(\d{1,2})", s)
    if not m or len(s) > 24:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}"


def ymd(d):
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def base_name(nm):
    return re.sub(r"^\s*(\[[^\]]*\]\s*)+", "", nm or "").strip()


def decision_name(nm):
    """묶음 기준 이름: 주요사항보고서(영업양도결정)와 거래소 공시 영업양도결정을 같은 결정으로 본다."""
    b = base_name(nm)
    m = re.match(r"주요사항보고서\s*\((.+)\)$", b)
    return re.sub(r"\s+", "", m.group(1) if m else b)


def build_groups(events):
    by, out = {}, []
    for e in sorted(events, key=lambda e: e["rcept_no"]):
        lst = by.setdefault((e["corp_code"], decision_name(e["report_nm"])), [])
        # 새 원공시는 새 묶음. 다만 거래소 공시와 주요사항보고서가 2주 안에 짝으로 나오면 같은 결정이다
        pair = lst and (e.get("krx") or any(x.get("krx") for x in lst[-1])) and \
            (datetime.strptime(e["rcept_dt"], "%Y%m%d") - datetime.strptime(lst[-1][-1]["rcept_dt"], "%Y%m%d")).days <= 14 and \
            not (e.get("krx") and any(x.get("krx") and not x.get("amended") for x in lst[-1]))
        if not lst or (not e.get("amended") and not pair):
            lst.append([])
        lst[-1].append(e)
    for items_list in by.values():
        for items in items_list:
            items.sort(key=lambda e: e["rcept_no"], reverse=True)
            out.append(items)
    return out


def pick_row(rows, rcepts, latest):
    if not rows:
        return None
    hit = [r for r in rows if r.get("rcept_no") in rcepts]
    if hit:
        return max(hit, key=lambda r: r.get("rcept_no", ""))
    ok = [r for r in rows if r.get("rcept_no", "") <= latest + "999999"] or rows
    return max(ok, key=lambda r: r.get("rcept_no", ""))


def trim(row):
    return {k: (v[:TEXT_MAX] + "…" if isinstance(v, str) and len(v) > TEXT_MAX else v)
            for k, v in row.items() if k not in ("corp_cls", "corp_code", "corp_name")}


def fetch_detail(key, api, corp_code, first, latest, rcepts):
    bgn = (datetime.strptime(first, "%Y%m%d") - timedelta(days=150)).strftime("%Y%m%d")
    d = call(api, {"corp_code": corp_code, "bgn_de": bgn, "end_de": latest}, key)
    if d.get("status") != "000":
        return None
    row = pick_row(d.get("list", []), rcepts, latest)
    return trim(row) if row else None


def fetch_estk(key, corp_code, first):
    """증권신고서(지분증권): 최초 신고서의 발행가(예정)와 최신 신고서의 발행가·일정·주관사."""
    # 증권신고서 API는 최초 신고서 접수일로 걸러서, 결정 공시보다 한참 앞에서부터 찾아야 한다
    bgn = (datetime.strptime(first, "%Y%m%d") - timedelta(days=150)).strftime("%Y%m%d")
    end = datetime.now(KST).strftime("%Y%m%d")
    d = call("estkRs", {"corp_code": corp_code, "bgn_de": bgn, "end_de": end}, key)
    if d.get("status") != "000":
        return None
    groups = {g.get("title"): g.get("list", []) for g in d.get("group", [])}
    gen = groups.get("일반사항", [])
    kinds = [r for r in groups.get("증권의종류", []) if "보통" in r.get("stksen", "")] or groups.get("증권의종류", [])
    if not gen and not kinds:
        return None
    latest = max([r["rcept_no"] for r in gen + kinds])
    first_k = min(kinds, key=lambda r: r["rcept_no"]) if kinds else {}
    last_k = max(kinds, key=lambda r: r["rcept_no"]) if kinds else {}
    last_g = max(gen, key=lambda r: r["rcept_no"]) if gen else {}
    uw = sorted({r.get("actnmn", "") for r in groups.get("인수인정보", []) if r.get("rcept_no") == latest and r.get("actnmn")})
    return {
        "rcept_no": latest, "versions": len({r["rcept_no"] for r in gen + kinds}),
        "plan_price": first_k.get("slprc"), "last_price": last_k.get("slprc"),
        "count": last_k.get("stkcnt"), "amount": last_k.get("slta"), "method": last_k.get("slmthn"),
        "subscription": last_g.get("sbd"), "payment": last_g.get("pymd"), "record": last_g.get("asstd"),
        "allot_notice": last_g.get("asand"), "underwriters": uw,
    }


def latest_offer_doc(key, corp_code, first):
    """가장 최근 증권신고서(지분증권) 접수번호 (발행조건확정·기재정정 포함)."""
    bgn = (datetime.strptime(first, "%Y%m%d") - timedelta(days=150)).strftime("%Y%m%d")
    end = datetime.now(KST).strftime("%Y%m%d")
    d = call("list", {"corp_code": corp_code, "bgn_de": bgn, "end_de": end, "pblntf_ty": "C", "page_count": 100}, key)
    docs = [x for x in d.get("list", []) if "증권신고서(지분증권)" in x.get("report_nm", "")]
    if not docs:
        return None
    docs.sort(key=lambda x: x["rcept_no"], reverse=True)
    x = docs[0]
    return {"rcept_no": x["rcept_no"], "report_nm": x["report_nm"], "date": ymd(x["rcept_dt"]),
            "older": [o["rcept_no"] for o in docs[1:4]]}   # 매매기간이 최신 정정본에 없을 때 찾아볼 이전 신고서


DATE_RE = r"(20\d{2})\s*[년.\-/]\s*(\d{1,2})\s*[월.\-/]\s*(\d{1,2})"


def doc_text(key, rcept_no):
    """공시 원문(zip 안의 XML)을 태그 없는 한 줄 텍스트로."""
    try:
        time.sleep(0.12)
        with urllib.request.urlopen(f"{API}/document.xml?crtfc_key={key}&rcept_no={rcept_no}", timeout=40) as r:
            z = zipfile.ZipFile(io.BytesIO(r.read()))
    except Exception as e:
        print(f"document {rcept_no}: {e}", file=sys.stderr)
        return None
    text = ""
    for n in z.namelist():
        raw = z.read(n)
        for enc in ("utf-8", "euc-kr", "cp949"):
            try:
                text += raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))


def offer_price_from_doc(key, rcept_no):
    """신고서 원문의 모집조건 표(보통주 수량·액면가·모집가액·모집총액)에서 1주당 모집가액을 읽는다.
    수량 × 가액 = 총액이 맞는 행만 인정하고, 정정 후 값이 뒤에 오므로 마지막 행을 쓴다."""
    plain = doc_text(key, rcept_no)
    if not plain:
        return None
    hit = None
    for m in re.finditer(r"보통주\s*([\d,]{3,})\s+([\d,]{1,7})\s+([\d,]{1,9})\s+([\d,]{5,})", plain):
        cnt, price, total = (num(m.group(i)) for i in (1, 3, 4))
        if cnt and price and total and abs(cnt * price - total) <= max(1000, total * 0.001):
            hit = int(price)
    final = bool(re.search(r"(최종|확정)\s*발행가액을?\s*기준으로\s*산정", plain)) if hit else False
    stage = "확정" if final else None
    if hit and not final:   # 정정사유 머리말: '1차 발행가액 확정에 따른 정정' / '[2차발행가액]'
        head = plain[:4000]
        stage = "2차" if re.search(r"2\s*차\s*발행\s*가액", head) else "1차" if re.search(r"1\s*차\s*발행\s*가액", head) else "예정"
    cands = rights_trading_period(plain)
    gp = public_offer_period(plain)
    if hit is None and not cands and not gp:
        return None
    out = {"price": hit, "final": final, "stage": stage, "rt_cands": cands, "gp_cands": gp, "rt_start": None}
    if not cands:   # 표현 확인용: '신주인수권증서' 주변 날짜가 있는 구간 몇 개
        snips = [plain[max(0, m.start() - 20): m.end() + 90] for m in re.finditer(r"신주인수권\s*증서", plain)]
        out["rt_snip"] = [x for x in snips if re.search(DATE_RE, x)][:4] or snips[:2]
    return out


def period_after(plain, label_re, window=90):
    """label 뒤 '날짜 ~ 날짜' (끝 날짜에 연도가 없어도 됨). 날짜가 붙은 첫 등장을 쓴다."""
    for m in re.finditer(label_re, plain):
        seg = plain[m.end(): m.end() + window]
        d1 = re.search(DATE_RE, seg)
        if not d1:
            continue
        y, mo, d = (int(x) for x in d1.groups())
        start = f"{y:04d}-{mo:02d}-{d:02d}"
        m2 = re.match(r"\s*일?\s*(?:\([^)]{0,6}\))?\s*(?:부터)?\s*[~∼\-–]\s*(?:(20\d{2})\s*[년.\-/]\s*)?(\d{1,2})\s*[월.\-/]\s*(\d{1,2})", seg[d1.end(): d1.end() + 40])
        if not m2:
            return start, None
        y2 = int(m2.group(1)) if m2.group(1) else y
        mo2, d2 = int(m2.group(2)), int(m2.group(3))
        if not m2.group(1) and mo2 < mo:   # 12월 → 1월로 넘어가는 경우
            y2 += 1
        return start, f"{y2:04d}-{mo2:02d}-{d2:02d}"
    return None, None


def dates_after(plain, label_re, window):
    """label 바로 뒤 window 글자 안의 날짜들 (날짜가 붙은 첫 등장 기준)."""
    for m in re.finditer(label_re, plain):
        seg = plain[m.end(): m.end() + window]
        got = [f"{int(y):04d}-{int(mo):02d}-{int(d):02d}" for y, mo, d in re.findall(DATE_RE, seg)
               if 1 <= int(mo) <= 12 and 1 <= int(d) <= 31]
        if got:
            return got
    return []


def rights_trading_period(plain):
    """'신주인수권증서' 근처(뒤 100자 안, 매매·거래·상장이라는 말과 함께)에 나오는 날짜 구간 후보를 모두 모은다.
    어느 후보가 맞는지는 이벤트 일정(배정기준일·청약일)과 비교해 summarize에서 고른다."""
    cands = []
    def add(c):   # 다시 나오면 뒤로 보낸다: 정정 신고서는 '정정 전 → 정정 후 → 본문' 순이라 마지막 등장이 최신 값
        if c in cands:
            cands.remove(c)
        cands.append(c)
    def iso(y, mo, d):
        y, mo, d = int(y), int(mo), int(d)
        if y < 100:
            y += 2000
        return f"{y:04d}-{mo:02d}-{d:02d}" if 1 <= mo <= 12 and 1 <= d <= 31 else None
    for m in re.finditer(r"신주인수권\s*증서", plain):
        seg = plain[m.end(): m.end() + 100]
        # (가) 일정표: '2026년 11월 10일 ~2026년 11월 16일 신주인수권증서 상장 거래기간' — 날짜가 앞에 온다
        before = plain[max(0, m.start() - 60): m.start()]
        bm = re.search(DATE_RE + r"\s*일?\s*~\s*(?:(20\d{2})\s*[년.\-/]\s*)?(\d{1,2})\s*[월.\-/]\s*(\d{1,2})\s*일?\s*$", before)
        if bm and re.search(r"매매|거래|상장", seg[:40]):
            s0 = iso(*bm.group(1, 2, 3))
            e0 = iso(bm.group(4) or bm.group(1), bm.group(5), bm.group(6))
            if s0 and e0:
                add([s0, e0])
        if not re.search(r"매매|거래|상장", seg[:70]):
            continue
        # (나) 두 자리 연도: '상장일 5거래일 이상 동안 거래(26.09.28~26.10.02)'. 정정 강조 표시 때문에 '26.10. 23 ~26.10.30'처럼 공백이 끼기도 한다
        tm = re.search(r"(\d{2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})\s*[~∼\-–]\s*(\d{2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})", seg)
        if tm:
            s0, e0 = iso(*tm.group(1, 2, 3)), iso(*tm.group(4, 5, 6))
            if s0 and e0:
                add([s0, e0])
        d1 = re.search(DATE_RE, seg)
        if not d1:
            continue
        y, mo, d = (int(x) for x in d1.groups())
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            continue
        start, end = f"{y:04d}-{mo:02d}-{d:02d}", None
        m2 = re.match(r"\s*일?\s*(?:\([^)]{0,6}\))?\s*(?:부터)?\s*[~∼\-–]\s*(?:(20\d{2})\s*[년.\-/]\s*)?(\d{1,2})\s*[월.\-/]\s*(\d{1,2})", seg[d1.end(): d1.end() + 40])
        if m2:
            y2 = int(m2.group(1)) if m2.group(1) else y
            mo2, dd2 = int(m2.group(2)), int(m2.group(3))
            if not m2.group(1) and mo2 < mo:
                y2 += 1
            if 1 <= mo2 <= 12 and 1 <= dd2 <= 31:
                end = f"{y2:04d}-{mo2:02d}-{dd2:02d}"
        add([start, end])
    return cands[-16:]


TENDER_V = 2    # 파서가 바뀌면 올려서 기존 캐시를 다시 읽게 한다
APDOC_V = 2
OFFER_V = 6
MDOC_V = 2


def public_offer_period(plain):
    """주주배정 후 실권주 일반공모의 '일반공모 청약' 기간 후보. 신고서에 세 가지로 적힌다:
    일정표 '2026-12-07 ~2026-12-08 일반공모 청약', 요약 '일반공모의 청약기일은 2026년 10월 20일 ~ 2026년 10월 21일'
    ('일반공모 청약 예정일은 … 과 …', '… 및 12월 18일' 포함), 모집조건표 '일반모집 또는 매출 개시일 … 종료일 …'.
    어느 것이 맞는지는 summarize에서 구주주 청약·납입일과 비교해 고른다."""
    cands = []
    def iso(y, mo, d):
        y, mo, d = int(y), int(mo), int(d)
        return f"{y:04d}-{mo:02d}-{d:02d}" if 1 <= mo <= 12 and 1 <= d <= 31 else None
    def add(a, b):
        if a and b and [a, b] not in cands:
            cands.append([a, b])
    END = r"(?:(20\d{2})\s*[년.\-/]\s*)?(\d{1,2})\s*[월.\-/]\s*(\d{1,2})"
    for m in re.finditer(r"일반\s*(?:공모|모집)", plain):
        after = plain[m.end(): m.end() + 30]
        if re.match(r"\s*(?:의\s*)?청약\s*(?:공고|결과)", after):   # 청약 공고·결과는 기간이 아니다
            continue
        before = plain[max(0, m.start() - 50): m.start()]
        bm = re.search(DATE_RE + r"\s*일?\s*[~∼]\s*" + END + r"\s*일?\s*$", before)
        if bm and re.match(r"\s*청약", after):
            add(iso(*bm.group(1, 2, 3)), iso(bm.group(4) or bm.group(1), bm.group(5), bm.group(6)))
        seg = plain[m.end(): m.end() + 90]
        if not re.match(r"\s*(?:의\s*)?(?:청약|또는\s*매출)", seg):
            continue
        d1 = re.search(DATE_RE, seg)
        if not d1:
            continue
        m2 = re.match(r"\s*일?\s*(?:\([^)]{0,6}\))?\s*(?:부터)?\s*(?:[~∼\-–]|과|와|및|종료일)\s*" + END, seg[d1.end(): d1.end() + 40])
        if m2:
            add(iso(*d1.groups()), iso(m2.group(1) or d1.group(1), m2.group(2), m2.group(3)))
    return cands[:12]


def tender_from_doc(key, rcept_no):
    """공개매수신고서 원문에서 공개매수가격·기간·결제일을 읽는다.
    가격은 '2. 공개매수의 조건' 표(매수가격 매수예정수량 지급방법 → '보통주 (주당) N원 최대 M주')에서 읽고,
    없으면 산정근거 첫 문장('공개매수가격인 주당 금 N원'), 그래도 없으면 '공개매수가격' 뒤 금액의 빈도로 정한다.
    산정근거의 '종가(금 N원)'·'평균주가(금 N원)' 같은 비교 금액은 쓰지 않는다. 정정본은 본문이 뒤에 오므로 마지막 표를 쓴다."""
    plain = doc_text(key, rcept_no)
    if not plain:
        return None
    exact = None
    for m in re.finditer(r"매수가격\s*매수예정\s*수량\s*지급\s*방법(.{0,160}?)(\d{1,3}(?:,\d{3})+|\d{3,})\s*원\s*(?:\(?\s*)?최대", plain):
        exact = int(m.group(2).replace(",", ""))
    if exact is None:
        for m in re.finditer(r"(?:공개매수가격(?:인|은|을)?\s*(?:1주당|주당)\s*(?:금\s*)?|주당\s*공개매수가격(?:은|인)?\s*(?:금\s*)?)(\d{1,3}(?:,\d{3})+|\d{3,})\s*원", plain):
            exact = int(m.group(1).replace(",", ""))
    seen = {}
    for m in re.finditer(r"공개매수\s*가격", plain):
        seg = plain[m.end(): m.end() + 80]
        p = re.search(r"(\d{1,3}(?:,\d{3})+|\d{4,})\s*원", seg)
        if p:
            v = int(p.group(1).replace(",", ""))
            if v >= 100:
                seen[v] = seen.get(v, 0) + 1
    period = dates_after(plain, r"공개매수\s*기간", 120)
    settle = dates_after(plain, r"결제\s*일", 60)
    price = exact or (max(seen, key=lambda v: seen[v]) if seen else None)
    if price is None and not period:
        return None
    # 조건표에서 읽었으면 확실하다: 다른 후보를 남기지 않는다
    return {"rcept_no": rcept_no, "price": price, "exact": exact is not None, "v": TENDER_V,
            "candidates": [] if exact else sorted(seen, key=lambda v: -seen[v])[:4],
            "start": period[0] if period else None, "end": period[1] if len(period) > 1 else None,
            "settle": settle[0] if settle else None}


def appraisal_from_doc(key, rcept_no, corp_name=""):
    """주요사항보고서 원문의 '주식매수청구권에 관한 사항'에서 매수예정가격·청구기간·반대의사 통지기간을 읽는다.
    (회사분할 결정 API에는 매수청구 항목이 없어 물적분할은 원문에서만 알 수 있다)"""
    plain = doc_text(key, rcept_no)
    if not plain:
        return None
    exact = None
    # (가) 합병 당사회사가 둘이면 '(주)회사명의 보통주식 ... 제시가격 N원'처럼 회사별로 적힌다
    short = re.sub(r"^\(주\)|\(주\)$|주식회사|㈜|\s", "", corp_name or "")
    if short:
        for m in re.finditer(r"(?:\(주\)|㈜|주식회사)?\s*" + re.escape(short) + r"\s*(?:\(주\)|㈜)?\s*의\s*보통주식?(.{0,160}?)제시\s*가격\s*(\d{1,3}(?:,\d{3})+|\d{3,})\s*원", plain):
            exact = int(m.group(2).replace(",", ""))
    # (나) 주요사항보고서 표 칸: '매수예정가격 9,900' (원 생략) — 숫자가 바로 붙어 나온다
    if exact is None:
        for m in re.finditer(r"매수\s*예정\s*가격\s*(?:\(\s*원\s*\))?\s*:?\s*(\d{1,3}(?:,\d{3})+|\d{3,})(?!\s*[%주])", plain):
            exact = int(m.group(1).replace(",", ""))
            break
    seen = {}
    for m in re.finditer(r"(?:주식\s*)?매수\s*(?:예정\s*)?가격|주식\s*매수\s*청구\s*가격", plain):
        seg = plain[m.end(): m.end() + 120]
        p = re.search(r"(\d{1,3}(?:,\d{3})+|\d{4,})\s*원", seg)
        if p:
            v = int(p.group(1).replace(",", ""))
            if v >= 100:
                seen[v] = seen.get(v, 0) + 1
    period = dates_after(plain, r"(?:주식\s*)?매수\s*청구\s*(?:권\s*)?(?:의\s*)?(?:행사\s*)?기간", 140)
    notice = dates_after(plain, r"반대\s*의사\s*(?:의\s*)?(?:서면\s*)?통지\s*(?:접수\s*)?기간", 140)
    price = exact or (max(seen, key=lambda v: seen[v]) if seen else None)
    if price is None and not period:
        return None
    return {"rcept_no": rcept_no, "price": price, "exact": exact is not None, "v": APDOC_V,
            "terminated": terminated(plain),
            "candidates": [] if exact else sorted(seen, key=lambda v: -seen[v])[:4],
            "ap_start": period[0] if period else None, "ap_end": period[1] if len(period) > 1 else None,
            "notice_start": notice[0] if notice else None, "notice_end": notice[1] if len(notice) > 1 else None}


def terminated(plain):
    """정정신고 머리(정정사유 표)에 계약 해지·결정 철회가 적혀 있으면 사실상 끝난 이벤트다."""
    head = plain[:3000].split("귀중")[0]
    return bool(re.search(r"(?<!소비대차)계약\s*(?:의\s*)?(?:해지|해제)|결정\s*(?:의\s*)?철회|철회\s*결정|진행\s*중단", head))


def amend_check(key, rcept_no):
    """매수청구가가 API에 있는 이벤트도 정정 사유가 해지·철회인지 원문 머리만 본다."""
    plain = doc_text(key, rcept_no)
    return None if plain is None else {"rcept_no": rcept_no, "terminated": terminated(plain)}


def rights_from_main(key, rcept_no):
    """유상증자 주요사항보고서 원문: 증자방식과 '6. 신주 발행가액 보통주식 (원) N' (제3자배정은 이사회에서 확정)."""
    plain = doc_text(key, rcept_no)
    if not plain:
        return None
    m = re.search(r"증자\s*방식\s*([가-힣0-9]+(?:\s*실권주\s*일반공모)?)", plain)
    p = re.search(r"신주\s*발행가액\s*보통주식\s*\(\s*원\s*\)\s*(\d{1,3}(?:,\d{3})+|\d{2,})", plain)
    out = {"rcept_no": rcept_no, "v": MDOC_V, "method": m.group(1) if m else None,
           "price": int(p.group(1).replace(",", "")) if p else None}
    # 일정 칸: '9. 청약예정일 … 구주주 시작일 D 종료일 D … 일반공모 시작일 D 종료일 D 10. 납입일 D … 신주의 상장예정일 D'
    def d(x):
        q = re.match(r"\s*" + DATE_RE, x)
        return f"{int(q.group(1)):04d}-{int(q.group(2)):02d}-{int(q.group(3)):02d}" if q and 1 <= int(q.group(2)) <= 12 and 1 <= int(q.group(3)) <= 31 else None
    sec = re.search(r"청약\s*예정일(.{0,400})", plain)
    if sec:
        for who, key2 in (("구주주", "sh"), ("일반공모", "gp")):
            w = re.search(who + r"\s*시작일\s*(.{0,20}?)\s*종료일\s*(.{0,20})", sec.group(1))
            if w and d(w.group(1)):
                out[key2] = [d(w.group(1)), d(w.group(2)) or d(w.group(1))]
    pay = re.search(r"\d+\.\s*납입일\s*(.{0,20})", plain)
    lst = re.search(r"신주의\s*상장\s*예정일\s*(.{0,20})", plain)
    out["pay"] = d(pay.group(1)) if pay else None
    out["list"] = d(lst.group(1)) if lst else None
    return out


def num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def fetch_prices(codes):
    """네이버 금융 실시간 시세를 40종목씩 묶어서 조회."""
    out, codes = {}, sorted({c for c in codes if c})
    sign = {"1": 1, "2": 1, "3": 0, "4": -1, "5": -1}
    for i in range(0, len(codes), 40):
        url = "https://polling.finance.naver.com/api/realtime/domestic/stock/" + ",".join(codes[i:i + 40])
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                datas = json.load(r).get("datas", [])
        except Exception as e:
            print(f"price: {e}", file=sys.stderr)
            continue
        for x in datas:
            px, chg = num(x.get("closePrice")), num(x.get("compareToPreviousClosePrice"))
            s = sign.get((x.get("compareToPreviousPrice") or {}).get("code"), 0)
            if px is None:
                continue
            chg = (chg or 0) * s
            out[x.get("itemCode")] = {
                "price": px, "prev": px - chg, "chg": chg,
                "pct": round(chg / (px - chg) * 100, 2) if px - chg else 0,
                "status": x.get("marketStatus"), "at": (x.get("localTradedAt") or "")[:16],
            }
    # 시가총액(억 원): 실시간 시세에는 없어 종목별 통합 정보에서 읽는다
    for code in out:
        out[code]["mcap"] = market_cap(code)
    return out


def eok(text):
    """'11조 2,505억' / '846억' → 억 원 단위 숫자."""
    if not text:
        return None
    t = str(text).replace(",", "").replace(" ", "")
    jo = re.search(r"([\d.]+)조", t)
    ek = re.search(r"([\d.]+)억", t)
    if not jo and not ek:
        return None
    return round(float(jo.group(1)) * 10000 if jo else 0) + round(float(ek.group(1)) if ek else 0)


def market_cap(code):
    try:
        time.sleep(0.05)
        req = urllib.request.Request(f"https://m.stock.naver.com/api/stock/{code}/integration", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            infos = json.load(r).get("totalInfos", [])
    except Exception as e:
        print(f"mcap {code}: {e}", file=sys.stderr)
        return None
    return next((eok(x.get("value")) for x in infos if x.get("code") == "marketValue"), None)


def summarize(items, det, estk, today, tender=None, apdoc=None, mdoc=None):
    top = items[0]
    rcepts = {e["rcept_no"] for e in items}
    first = min(e["rcept_dt"] for e in items)
    nm = top["report_nm"]
    g = {
        "id": top["rcept_no"], "k": top["type"], "n": top["corp_name"], "c": top["corp_code"],
        "s": top["stock_code"], "m": top["market"], "nm": nm, "d": ymd(top["rcept_dt"]), "d0": ymd(first),
        "f": top.get("flags", []), "ap": top.get("appraisal", "?"),
        "amends": sum(1 for e in items if e.get("amended")),
        # fs: 수집기가 처음 본 시각 (HH:MM). 접수일 당일에 잡은 경우만 남긴다 (과거 채우기 분은 실제 시각이 아님)
        "items": [{"r": e["rcept_no"], "d": ymd(e["rcept_dt"]), "nm": e["report_nm"], "x": bool(e.get("amended")),
                   "fs": e["first_seen"][11:16] if e.get("first_seen", "")[:10] == ymd(e["rcept_dt"]) else ""} for e in items],
    }
    if g["ap"] == "?":
        g["ap"] = next((e.get("appraisal") for e in items if e.get("appraisal") not in (None, "?")), "?")
    row = (det or {}).get("row") or {}
    g["detail"] = [[lab, row[k]] for k, lab in LABELS if row.get(k) not in (None, "", "-")]
    sched = []
    for k, lab in SCHEDULE:
        dt = to_date(row.get(k))
        if dt:
            sched.append([lab, dt, k])
    if estk:
        for lab, v in (("청약 시작", estk.get("subscription")), ("납입일", estk.get("payment")), ("배정기준일", estk.get("record"))):
            dt = to_date(str(v).split("~")[0]) if v else None
            if dt:
                sched.append([lab, dt, "estk"])
        if estk.get("subscription") and "~" in estk["subscription"]:
            dt = to_date(estk["subscription"].split("~")[1])
            if dt:
                sched.append(["청약 종료", dt, "estk"])
        pay = to_date(estk.get("payment"))
        if pay:  # 신주 상장일은 공시 항목에 없어 납입일 + 14일로 추정
            sched.append(["신주 상장(추정)", (datetime.strptime(pay, "%Y-%m-%d") + timedelta(days=14)).strftime("%Y-%m-%d"), "est"])
    if apdoc:   # 원문에서 읽은 매수청구·반대의사 기간 (공시 항목에 같은 일정이 없을 때만)
        have = {lab for lab, _, _ in sched}
        for lab, k in (("매수청구 시작", "ap_start"), ("매수청구 종료", "ap_end"), ("반대의사 통지 시작", "notice_start"), ("반대의사 통지 종료", "notice_end")):
            if apdoc.get(k) and lab not in have:
                sched.append([lab, apdoc[k], "apd"])
    doc = (det or {}).get("doc") or {}
    if doc.get("rt_cands"):   # 신고서 원문의 신주인수권증서 매매기간 후보 중 이 이벤트 일정에 맞는 것
        rec = next((b for a, b, k in sched if a == "배정기준일"), None)
        sub = next((b for a, b, k in sched if a == "청약 시작"), None)
        floor = (datetime.strptime(first, "%Y%m%d") - timedelta(days=10)).strftime("%Y-%m-%d")
        def fits(c):
            s0, e0 = c
            if not e0 or s0 < floor:   # 끝 날짜까지 있는 구간만, 그리고 최초 공시 이전 날짜는 버린다
                return False
            if rec and s0 < rec:
                return False
            if sub and (e0 or s0) >= sub:
                return False
            if e0 and not (0 <= (datetime.strptime(e0, "%Y-%m-%d") - datetime.strptime(s0, "%Y-%m-%d")).days <= 21):
                return False
            return bool(rec or sub)   # 비교할 일정이 하나도 없으면 믿지 않는다
        # 후보는 최신 신고서 → 이전 신고서 순으로 이어 붙였다 (rt_bounds: 신고서별 끝 위치).
        # 맞는 후보가 있는 가장 최신 신고서에서, 원문에 가장 뒤에 나온 것(정정 전 → 정정 후 → 본문 순이라 최신 값)을 쓴다
        bounds = doc.get("rt_bounds") or [len(doc["rt_cands"])]
        pick = next((c for lo, hi in zip([0] + bounds, bounds) for c in reversed(doc["rt_cands"][lo:hi]) if fits(c)), None)
        if pick:
            sched.append(["신주인수권증서 매매 시작", pick[0], "doc"])
            if pick[1]:
                sched.append(["신주인수권증서 매매 종료", pick[1], "doc"])
    if doc.get("gp_cands"):   # 실권주 일반공모 청약: 구주주 청약이 끝난 뒤, 납입일 이전의 7일 이내 구간
        sub_end = next((b for a, b, k in sched if a == "청약 종료"), None) or next((b for a, b, k in sched if a == "청약 시작"), None)
        pay = next((b for a, b, k in sched if a == "납입일"), None)
        def gp_fits(c):
            s0, e0 = c
            if not sub_end or s0 <= sub_end or (pay and e0 > pay):
                return False
            return 0 <= (datetime.strptime(e0, "%Y-%m-%d") - datetime.strptime(s0, "%Y-%m-%d")).days <= 7
        pick = next((c for c in doc["gp_cands"] if gp_fits(c)), None)
        if pick:
            sched.append(["일반공모 청약 시작", pick[0], "doc"])
            sched.append(["일반공모 청약 종료", pick[1], "doc"])
    if mdoc:   # 주요사항보고서 본문 일정 (증권신고서가 없거나 아직 일정이 비어 있을 때만)
        have = {a for a, _, _ in sched}
        sub = mdoc.get("sh") or mdoc.get("gp")
        if sub and "청약 시작" not in have:
            sched += [["청약 시작", sub[0], "mdoc"], ["청약 종료", sub[1], "mdoc"]]
        if mdoc.get("sh") and mdoc.get("gp") and "일반공모 청약 시작" not in have:
            sched += [["일반공모 청약 시작", mdoc["gp"][0], "mdoc"], ["일반공모 청약 종료", mdoc["gp"][1], "mdoc"]]
        if mdoc.get("pay") and "납입일" not in have:
            sched.append(["납입일", mdoc["pay"], "mdoc"])
        if mdoc.get("list") and not any("상장" in a for a in have):
            sched.append(["신주 상장예정일", mdoc["list"], "mdoc"])
    if tender:
        for lab, k in (("공개매수 시작", "start"), ("공개매수 종료", "end"), ("결제일", "settle")):
            if tender.get(k):
                sched.append([lab, tender[k], "tdr"])
    sched.sort(key=lambda x: x[1])
    g["sched"] = [[a, b] for a, b, _ in sched]

    # 종료일과 진행 여부
    ends = [b for a, b, k in sched if k not in END_EXCLUDE]
    if re.search(r"결과보고서|철회|해제|취소", base_name(nm)):
        g["end"], g["end_basis"] = g["d"], "결과·철회·해제 공시"
    elif ends:
        g["end"] = max(ends)
        g["end_basis"] = next(a for a, b, k in sched if b == g["end"] and k not in END_EXCLUDE)
    else:
        g["end"] = (datetime.strptime(first, "%Y%m%d") + timedelta(days=FALLBACK_DAYS)).strftime("%Y-%m-%d")
        g["end_basis"] = f"일정 정보 없음 (공시 후 {FALLBACK_DAYS}일)"
    g["status"] = "active" if g["end"] >= today else "past"

    # 표에 쓰는 핵심 값
    mx = {}
    if row.get("ic_mthn"):
        mx["method"] = row["ic_mthn"]
    if estk:
        mx.update({k: estk.get(k) for k in ("plan_price", "last_price", "amount", "count", "subscription", "payment", "underwriters", "versions")})
        mx["method"] = estk.get("method") or mx.get("method")
    if apdoc and apdoc.get("price") and "appraisal_price" not in mx:
        mx["appraisal_price"] = f'{apdoc["price"]:,}'
        mx["appraisal_src"] = "원문"
        others = [v for v in apdoc.get("candidates", []) if v != apdoc["price"]]
        if others:
            mx["appraisal_alt"] = [f"{v:,}" for v in others]
    if tender and tender.get("price"):
        mx["tender_price"] = f'{tender["price"]:,}'
        others = [v for v in tender.get("candidates", []) if v != tender["price"]]
        if others:   # 원문에 다른 금액도 '공개매수가격' 옆에 나왔다: 화면에서 확인하도록 남긴다
            mx["tender_alt"] = [f"{v:,}" for v in others]
    if row.get("aprskh_plnprc") not in (None, "", "-"):
        mx["appraisal_price"] = row["aprskh_plnprc"]
    for k in ("mg_rt", "extr_rt", "nstk_ascnt_ps_ostk", "inh_prc", "trf_prc", "mgptncmp_cmpnm", "extr_tgcmp_cmpnm",
              "dvfcmp_cmpnm", "dlptn_cmpnm", "mg_stn", "extr_stn"):
        if row.get(k) not in (None, "", "-"):
            mx[k] = row[k][:200] if isinstance(row[k], str) else row[k]
    if top["type"] == "split" or "회사분할결정" in nm:   # 분할은 면제 사유 칸에 '단순ㆍ물적분할' 같은 방식이 적힌다
        form = row.get("ex_sm_r") or ""
        if "분할" in form:
            mx["split_form"] = form[:60]
    g["mx"] = mx
    g["api"] = (det or {}).get("api")
    return g


def apply_cancels(out, cancels):
    """철회·취소 공시를 원래 이벤트에 붙인다: 같은 회사·같은 유형에서 철회일 이전에 시작한 가장 최근 묶음을 끝낸다.
    (인적분할은 merge로 분류되므로 분할 철회는 split·merge를 모두 본다)"""
    for c in sorted(cancels, key=lambda e: e["rcept_no"]):
        target = (c.get("flags") or ["rights"])[0]
        kinds = {"split", "merge"} if target == "split" else {target}
        day = ymd(c["rcept_dt"])
        c = dict(c, report_nm=re.sub(r"\s+", " ", c["report_nm"]).replace("( ", "("))   # 거래소 공시명의 긴 공백 정리
        cands = [g for g in out if g["c"] == c["corp_code"] and g["k"] in kinds and g["d0"] <= day and not g.get("cancel")]
        if not cands:
            continue
        g = max(cands, key=lambda g: (g["d0"], g["id"]))
        g["cancel"] = {"r": c["rcept_no"], "d": day, "nm": c["report_nm"]}
        g["items"].insert(0, {"r": c["rcept_no"], "d": day, "nm": c["report_nm"], "x": False, "cx": True,
                              "fs": c["first_seen"][11:16] if c.get("first_seen", "")[:10] == day else ""})
        if g["status"] == "active" or g["end"] > day:
            g["end"], g["end_basis"], g["status"] = day, "철회 공시", "past"


def main():
    key = os.environ.get("DART_API_KEY")
    if not key:
        sys.exit("DART_API_KEY 환경변수가 없습니다.")
    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")
    with open(SRC, encoding="utf-8") as f:
        store = json.load(f)
    try:
        with open(CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    except FileNotFoundError:
        cache = {}

    cancels = [e for e in store["events"] if e["type"] == "cancel"]
    groups = build_groups([e for e in store["events"] if e["type"] != "cancel"])
    fetched = estk_n = 0
    out = []
    for items in groups:
        top = items[0]
        gid = "|".join([top["corp_code"], base_name(top["report_nm"]), items[-1]["rcept_no"]])
        c = cache.get(gid, {})
        api = next((a for pat, a in DETAIL_API if re.search(pat, top["report_nm"])), None)
        rcepts = {e["rcept_no"] for e in items}
        first = min(e["rcept_dt"] for e in items)
        # 새 정정공시가 붙었거나, 이전 조회가 비었으면 (최대 5회) 다시 조회
        retry = c.get("row") is None and c.get("tries", 0) < 5
        if api and (c.get("latest") != top["rcept_no"] or retry):
            row = fetch_detail(key, api, top["corp_code"], first, top["rcept_dt"], rcepts)
            tries = 0 if row else (c.get("tries", 0) + 1 if c.get("latest") == top["rcept_no"] else 1)
            c = dict(c, api=api, row=row, latest=top["rcept_no"], at=now.isoformat(timespec="minutes"), tries=tries)
            fetched += 1
        # 주주배정·일반공모 유상증자는 증권신고서로 발행가·일정을 보강 (진행 중이면 1시간마다 갱신)
        method = ((c.get("row") or {}).get("ic_mthn") or "")
        stale = not c.get("estk_at") or c.get("estk") is None or \
            datetime.fromisoformat(c["estk_at"]) < now - timedelta(minutes=ESTK_TTL_MIN)
        done = c.get("estk_end") and c["estk_end"] < today
        if top["type"] == "rights" and re.search(r"주주배정|일반공모", method):
            if stale and not done:
                c["estk"] = fetch_estk(key, top["corp_code"], first)
                c["estk_at"] = now.isoformat(timespec="minutes")
                estk_n += 1
        # 신고서 원문에서 현재 발행가(1차·2차·확정)를 읽는다. 최신 신고서가 바뀔 때만 원문을 내려받는다.
        # 매매기간 파서가 바뀐 뒤 아직 안 읽은 문서는 한 번 다시 읽는다 (최근 75일 안의 결정만)
        need_rt = (c.get("doc") or {}).get("v") != OFFER_V and top["rcept_dt"] >= (now - timedelta(days=75)).strftime("%Y%m%d")
        if top["type"] == "rights" and re.search(r"주주배정|일반공모", method) and (stale or need_rt) and not done:
            doc = latest_offer_doc(key, top["corp_code"], first)
            if doc and ((c.get("doc") or {}).get("rcept_no") != doc["rcept_no"] or need_rt):
                got = offer_price_from_doc(key, doc["rcept_no"]) or {"price": None, "final": False, "rt_cands": []}
                got["rt_bounds"] = [len(got.get("rt_cands", []))]   # 신고서별 후보 끝 위치 (최신 신고서부터)
                # [발행조건확정] 정정본에는 매매기간이 빠져 있는 경우가 많아, 구간이 없으면 이전 신고서에서 찾는다
                for older in doc.get("older", []):
                    if any(c2[1] for c2 in got.get("rt_cands", [])) and got.get("gp_cands"):
                        break
                    o = offer_price_from_doc(key, older) or {}
                    got["rt_cands"] = got.get("rt_cands", []) + [c2 for c2 in o.get("rt_cands", []) if c2 not in got.get("rt_cands", [])]
                    got["rt_bounds"].append(len(got["rt_cands"]))
                    got["gp_cands"] = (got.get("gp_cands") or []) + [c2 for c2 in o.get("gp_cands", []) if c2 not in (got.get("gp_cands") or [])]
                c["doc"] = dict(doc, **got, v=OFFER_V)
        # 공개매수신고서: 원문에서 가격·기간·결제일 (최신 신고서가 바뀔 때만 다시 읽는다)
        if top["type"] == "tender" and "공개매수신고서" in top["report_nm"]:
            t0 = c.get("tender") or {}
            if (t0.get("rcept_no") != top["rcept_no"] or t0.get("v") != TENDER_V) and c.get("tender_tries", 0) < 3:
                # 최신 신고서부터 읽고, 정정 신고서에 없는 항목(기간 등)은 이전 신고서에서 채운다 (최대 3건)
                t = None
                docs = [e for e in items if "공개매수신고서" in e["report_nm"]][:3]
                for e in docs:
                    r = tender_from_doc(key, e["rcept_no"])
                    if not r:
                        continue
                    if t is None:
                        t = r
                    else:
                        for f in ("price", "start", "end", "settle"):
                            if t.get(f) is None and r.get(f) is not None:
                                t[f] = r[f]
                        if not t.get("exact") and r.get("exact"):   # 정정본에 조건표가 없으면 이전 신고서 조건표 값을 쓴다
                            t.update(price=r["price"], exact=True, candidates=[])
                    if t.get("exact") and all(t.get(f) for f in ("price", "start", "end", "settle")):
                        break
                if t:
                    t["rcept_no"] = top["rcept_no"]
                    c["tender"], c["tender_tries"] = t, 0
                else:
                    c["tender_tries"] = c.get("tender_tries", 0) + 1
        # 매수청구권 이벤트인데 API에 매수청구가가 없으면 (물적분할 등) 원문에서 읽는다
        api_price = ((c.get("row") or {}).get("aprskh_plnprc") or "-").strip()
        ap_event = top.get("appraisal") in ("Y", "?") and top["type"] in ("split", "merge", "exchange", "transfer")
        a0 = c.get("apdoc") or {}
        if a0.get("v") != APDOC_V and c.get("apdoc_tries", 0) >= 3:
            c["apdoc_tries"] = 0
        if ap_event and api_price in ("", "-") and (a0.get("rcept_no") != top["rcept_no"] or a0.get("v") != APDOC_V) and c.get("apdoc_tries", 0) < 3:
            a = None
            for e in items[:3]:   # 정정 공시에 빠진 항목은 이전 공시에서 채운다
                r = appraisal_from_doc(key, e["rcept_no"], top["corp_name"])
                if not r:
                    continue
                if a is None:
                    a = r
                else:
                    for f in ("price", "ap_start", "ap_end", "notice_start", "notice_end"):
                        if a.get(f) is None and r.get(f) is not None:
                            a[f] = r[f]
                    if not a.get("exact") and r.get("exact"):
                        a.update(price=r["price"], exact=True, candidates=[])
                if a.get("exact") and a.get("ap_end"):
                    break
            if a:
                a["rcept_no"] = top["rcept_no"]
                c["apdoc"], c["apdoc_tries"] = a, 0
            else:
                c["apdoc_tries"] = c.get("apdoc_tries", 0) + 1
        # 매수청구가가 API에 있는 정정공시도 정정 사유가 계약 해지·철회인지 한 번 본다
        recent = top["rcept_dt"] >= (now - timedelta(days=120)).strftime("%Y%m%d")
        if ap_event and recent and "정정" in top["report_nm"] and not (c.get("apdoc") or {}).get("rcept_no") == top["rcept_no"] \
                and (c.get("amend") or {}).get("rcept_no") != top["rcept_no"]:
            c["amend"] = amend_check(key, top["rcept_no"]) or c.get("amend")
        # 증권신고서가 없는 유상증자(제3자배정·소액공모)나 상세 API가 빈 건은 주요사항보고서 본문에서 방식·발행가를 읽는다
        if top["type"] == "rights" and recent and ((c.get("mdoc") or {}).get("rcept_no") != top["rcept_no"]
                                                   or (c.get("mdoc") or {}).get("v") != MDOC_V):
            c["mdoc"] = rights_from_main(key, top["rcept_no"]) or c.get("mdoc")
        md0 = c.get("mdoc") if (c.get("mdoc") or {}).get("rcept_no") == top["rcept_no"] else None
        g = summarize(items, c, c.get("estk"), today, c.get("tender"), c.get("apdoc"), md0)
        md = c.get("mdoc") or {}
        if g["k"] == "rights" and md.get("rcept_no") == top["rcept_no"]:
            if md.get("method") and not g["mx"].get("method"):
                g["mx"]["method"] = md["method"]
            if md.get("price") and not g["mx"].get("last_price") and not g["mx"].get("plan_price"):
                g["mx"]["board_price"] = f'{md["price"]:,}'
        term = (c.get("apdoc") or {}).get("terminated") and (c.get("apdoc") or {}).get("rcept_no") == top["rcept_no"] \
            or (c.get("amend") or {}).get("terminated") and (c.get("amend") or {}).get("rcept_no") == top["rcept_no"]
        if term and g["status"] == "active":
            g["end"], g["end_basis"], g["status"] = g["d"], "정정공시: 계약 해지·결정 철회", "past"
        if c.get("doc") and c["doc"].get("price"):
            g["mx"]["last_price"] = f'{c["doc"]["price"]:,}'
            g["mx"]["price_final"] = c["doc"]["final"]
            g["mx"]["price_stage"] = c["doc"].get("stage") or ("확정" if c["doc"]["final"] else "예정")
            g["mx"]["price_doc"] = {"r": c["doc"]["rcept_no"], "nm": c["doc"]["report_nm"], "d": c["doc"]["date"]}
        if c.get("estk"):
            c["estk_end"] = g["end"]
        cache[gid] = c
        out.append(g)

    apply_cancels(out, cancels)
    active_codes = [g["s"] for g in out if g["status"] == "active"]
    prices = fetch_prices(active_codes)
    out.sort(key=lambda g: (g["d"], g["id"]), reverse=True)
    view = {
        "updated_at": now.isoformat(timespec="minutes"),
        "events_updated_at": store.get("updated_at"),
        "today": today,
        "prices": prices,
        "groups": out,
    }
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(view, f, ensure_ascii=False, separators=(",", ":"))
    act = sum(1 for g in out if g["status"] == "active")
    print(f"묶음 {len(out)}건 (진행 중 {act}), 상세 조회 {fetched}건, 증권신고서 {estk_n}건, 시세 {len(prices)}종목")


if __name__ == "__main__":
    main()
