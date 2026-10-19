"""OpenDART에서 7개 이벤트 공시를 수집해 data/dart_events.json에 쌓는다.

대상: 유상증자, 무상증자, 공개매수, 물적분할, 인수합병(합병·분할합병·인적분할·경영권 양수도),
      주식교환·이전, 영업양수도, BW 발행(공모)
주식매수청구권이 생기는 공시(합병, 분할합병, 주식교환·이전, 영업양수도, 상장사 물적분할)는
모두 수집하고 appraisal 필드로 표시한다.
환경변수: DART_API_KEY (필수), TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (선택, 새 공시 알림)
표준 라이브러리만 사용한다.
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://opendart.fss.or.kr/api"
KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "dart_events.json")
KEEP_DAYS = 120          # 이 기간보다 오래된 공시는 파일에서 뺀다
# 매 실행마다 오늘 + 직전 N일을 다시 훑는다 (늦게 올라온 공시 대비). 과거 채우기는 LOOKBACK_DAYS=90처럼 넘긴다.
# OpenDART는 회사 지정 없이 최대 3개월까지만 조회되므로 90일로 제한한다.
LOOKBACK_DAYS = min(int(os.environ.get("LOOKBACK_DAYS") or 1), 90)
MARKETS = {"Y": "KOSPI", "K": "KOSDAQ"}
RECHECK = 150            # 실행당 미판별 공시 재확인 최대 건수

# 공시 유형(pblntf_ty)별로 보고서명 패턴 → 이벤트 키
RULES = [
    ("B", r"유상증자결정|유무상증자결정", "rights"),
    ("B", r"무상증자결정", "bonus"),
    ("B", r"회사분할결정", "split"),            # 물적 → split, 인적 → merge (상세 API로 판별)
    ("B", r"회사분할합병결정", "merge"),
    ("B", r"회사합병결정", "merge"),
    ("B", r"주식교환.?이전결정", "exchange"),
    ("B", r"영업양수결정|영업양도결정", "transfer"),
    ("B", r"신주인수권부사채권발행결정", "bw"),  # 공모/사모는 상세 API로 판별
    ("D", r"공개매수신고서|공개매수결과보고서", "tender"),
    ("I", r"최대주주변경을수반하는주식양수도계약", "merge"),
]
# 상세 API: 판별에 필요한 보고서만 조회 (보고서명 패턴 → API)
DETAIL = [
    (r"회사분할결정", "cmpDvDecsn"),
    (r"신주인수권부사채권발행결정", "bdwtIsDecsn"),
    (r"회사합병결정", "cmpMgDecsn"),
]
# 주식매수청구권 기본값: 있음 "Y", 없음 "N", 건별 확인 "?"
APPRAISAL = {"rights": "N", "bonus": "N", "tender": "N", "bw": "N",
             "split": "Y", "exchange": "Y", "transfer": "Y", "merge": "Y"}


def call(path, params, key, retries=3):
    q = urllib.parse.urlencode(dict(params, crtfc_key=key))
    url = f"{API}/{path}.json?{q}"
    for i in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return json.load(r)
        except Exception as e:  # 네트워크 일시 오류는 재시도
            if i == retries - 1:
                raise
            print(f"retry {path}: {e}", file=sys.stderr)
            time.sleep(2 ** i)


def list_filings(key, ty, bgn, end):
    page, out = 1, []
    while True:
        d = call("list", {"bgn_de": bgn, "end_de": end, "pblntf_ty": ty,
                          "page_no": page, "page_count": 100}, key)
        st = d.get("status")
        if st == "013":       # 조회된 데이터 없음
            break
        if st != "000":
            raise RuntimeError(f"list {ty}: {st} {d.get('message')}")
        out += d.get("list", [])
        total = int(d.get("total_page", 1))
        if page % 50 == 0:
            print(f"  {ty} {page}/{total} 페이지", flush=True)
        if page >= total:
            break
        page += 1
    return out


def detail_text(key, api, corp_code, rcept_dt, rcept_no):
    """상세 API에서 해당 접수번호 행의 문자열 값을 모두 이어 붙여 돌려준다 (필드명 변화에 덜 민감)."""
    try:
        # 정정공시는 원 결정일이 더 이전이므로 접수일 기준 90일 전까지 넓혀 찾는다
        bgn = (datetime.strptime(rcept_dt, "%Y%m%d") - timedelta(days=90)).strftime("%Y%m%d")
        d = call(api, {"corp_code": corp_code, "bgn_de": bgn, "end_de": rcept_dt}, key)
    except Exception as e:
        print(f"detail {api} {rcept_no}: {e}", file=sys.stderr)
        return None
    if d.get("status") != "000":
        return None
    allrows = d.get("list", [])
    # 같은 접수번호가 있으면 그 행, 없으면 가장 최근 결정 한 건
    rows = [r for r in allrows if r.get("rcept_no") == rcept_no] or \
        sorted(allrows, key=lambda r: r.get("rcept_no", ""))[-1:]
    return " ".join(str(v) for r in rows for v in r.values() if isinstance(v, str))


def mentions(text, word):
    """text에 word가 나오되 '아님/해당없음' 같은 부정 표현이 바로 뒤따르지 않으면 True."""
    for m in re.finditer(word, text or ""):
        if not re.match(r"[^.\n]{0,20}?(아님|아니|않|해당\s*사항\s*없|해당\s*없|미해당)", text[m.end():]):
            return True
    return False


def classify(key, item, k):
    """(이벤트 키, flags, 매수청구권)을 돌려준다. None이면 제외."""
    nm = item.get("report_nm", "")
    api = next((a for pat, a in DETAIL if re.search(pat, nm)), None)
    text = detail_text(key, api, item["corp_code"], item["rcept_dt"], item["rcept_no"]) if api else ""
    if re.search(r"최대주주변경", nm):
        return k, ["경영권 양수도"] + (["계약 해제·취소"] if re.search(r"해제|취소", nm) else []), "N"
    if k == "split":
        if text is None:
            return k, ["분할방법 확인 필요"], "?"
        if "물적" in text:
            return "split", ["물적분할"], "Y"
        if "인적" in text:
            return "merge", ["인적분할"], "N"   # 인적분할은 매수청구권 없음 (분할합병은 별도 공시)
        return k, ["분할방법 확인 필요"], "?"
    if k == "bw":
        if text is None:
            return k, ["발행방법 확인 필요"], "N"
        if "사모" in text and "공모" not in text:
            return None
        return k, ["공모" if "공모" in text else "발행방법 확인 필요"], "N"
    if k == "merge":
        if re.search(r"분할합병", nm):
            return k, ["분할합병"], "Y"
        if text is None:
            return k, ["합병"], "?"
        if mentions(text, "소규모"):   # 소규모합병: 존속회사 주주는 매수청구권 없음
            return k, ["합병", "소규모합병"], "N"
        return k, ["합병", "간이합병"] if mentions(text, "간이") else ["합병"], "Y"
    if k == "exchange":
        return k, (["소규모 교환·이전"] if mentions(text, "소규모") else []), "Y"
    if k == "transfer":
        return k, ["영업양수" if "양수" in nm else "영업양도"], "Y"
    return k, [], APPRAISAL.get(k, "N")


def telegram(lines):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (tok and chat and lines):
        return
    body = urllib.parse.urlencode({"chat_id": chat, "text": "\n".join(lines),
                                   "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", body, timeout=15)
    except Exception as e:
        print(f"telegram: {e}", file=sys.stderr)


def main():
    key = os.environ.get("DART_API_KEY")
    if not key:
        sys.exit("DART_API_KEY 환경변수가 없습니다.")
    now = datetime.now(KST)
    bgn = (now - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    end = now.strftime("%Y%m%d")

    try:
        with open(OUT, encoding="utf-8") as f:
            store = json.load(f)
    except FileNotFoundError:
        store = {"events": []}
    seen = {e["rcept_no"]: e for e in store["events"]}
    migrated = False
    for e in seen.values():   # 이전 형식 데이터 보정
        if "appraisal" not in e:
            e["appraisal"] = APPRAISAL.get(e["type"], "?")
            migrated = True

    # 판별이 안 된 기존 공시를 다시 확인한다 (한 번에 최대 RECHECK건)
    rechecked = 0
    for e in list(seen.values()):
        if rechecked >= RECHECK:
            break
        stale_cancel = "최대주주변경" in e["report_nm"] and re.search(r"해제|취소", e["report_nm"]) \
            and "계약 해제·취소" not in e["flags"]
        if e["appraisal"] != "?" and not any("확인 필요" in f for f in e["flags"]) and not stale_cancel:
            continue
        rechecked += 1
        nm = e["report_nm"]
        base = "split" if "회사분할결정" in nm else "bw" if "신주인수권부" in nm else e["type"]
        res = classify(key, e, base)
        if res is None:
            del seen[e["rcept_no"]]
            migrated = True
            continue
        if list(res) != [e["type"], e["flags"], e["appraisal"]]:
            e.update(type=res[0], flags=res[1], appraisal=res[2])
            migrated = True
    if rechecked:
        print(f"미판별 재확인 {rechecked}건", flush=True)

    new = []
    print(f"조회 기간 {bgn}~{end}", flush=True)
    for ty in sorted({r[0] for r in RULES}):
        rows = list_filings(key, ty, bgn, end)
        print(f"{ty}: 공시 {len(rows)}건 조회", flush=True)
        for it in rows:
            if it.get("corp_cls") not in MARKETS or it["rcept_no"] in seen:
                continue
            nm = it.get("report_nm", "")
            k = next((rk for rt, pat, rk in RULES if rt == ty and re.search(pat, nm)), None)
            if not k:
                continue
            res = classify(key, it, k)
            if res is None:
                continue
            k, flags, appraisal = res
            ev = {
                "rcept_no": it["rcept_no"],
                "rcept_dt": it["rcept_dt"],
                "first_seen": now.isoformat(timespec="minutes"),
                "type": k,
                "corp_name": it.get("corp_name", ""),
                "corp_code": it.get("corp_code", ""),
                "stock_code": it.get("stock_code", ""),
                "market": MARKETS[it["corp_cls"]],
                "report_nm": nm.strip(),
                "filer": it.get("flr_nm", ""),
                "amended": bool(re.search(r"\[기재정정\]|\[첨부정정\]|\[발행조건확정\]", nm)),
                "flags": flags,
                "appraisal": appraisal,
                "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={it['rcept_no']}",
            }
            seen[ev["rcept_no"]] = ev
            new.append(ev)

    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y%m%d")
    events = sorted((e for e in seen.values() if e["rcept_dt"] >= cutoff),
                    key=lambda e: e["rcept_no"], reverse=True)
    if not new and not migrated and os.path.exists(OUT):
        print("새 공시 없음")
        return

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"updated_at": now.isoformat(timespec="minutes"), "events": events},
                  f, ensure_ascii=False, indent=1)
    print(f"새 공시 {len(new)}건, 보관 {len(events)}건")
    if LOOKBACK_DAYS > 3:   # 과거 채우기 실행은 알림을 보내지 않는다
        return
    telegram([f"[{e['market']}] {e['corp_name']} · {e['report_nm']}"
              + (" · 매수청구권" if e["appraisal"] == "Y" else "") + f"\n{e['url']}" for e in new])


if __name__ == "__main__":
    main()
