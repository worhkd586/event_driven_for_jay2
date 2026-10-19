"""일회성 조사: DART 상세 API·증권신고서 API·시세 API의 실제 응답 형태를 probe/out.json에 저장한다."""
import json, os, urllib.parse, urllib.request
from datetime import datetime, timedelta

KEY = os.environ["DART_API_KEY"]
SAMPLES = [  # (api, corp_code, rcept_dt)
    ("piicDecsn", "00615723", "20261019"),    # 유상증자 MSDI
    ("fricDecsn", "00605124", "20261007"),    # 무상증자 알파칩스
    ("cmpMgDecsn", "00111838", "20261016"),   # 합병 대원
    ("cmpDvDecsn", "00106119", "20261012"),   # 물적분할 금양
    ("cmpDvDecsn", "00258801", "20260821"),   # 인적분할 카카오
    ("stkExtrDecsn", "01042429", "20260930"), # 주식교환 넥써쓰
    ("bsnInhDecsn", "01014055", "20261016"),  # 영업양수 에스트래픽
    ("bsnTrfDecsn", "01116502", "20260916"),  # 영업양도 아티스트컴퍼니
    ("bdwtIsDecsn", "00132868", "20261016"),  # BW 우성머티리얼스
    ("cmpDvmgDecsn", "00106119", "20261012"), # 분할합병 (없을 수 있음)
    ("estkRs", "00615723", "20261019"),       # 증권신고서(지분증권) MSDI
    ("mgRs", "00111838", "20261016"),         # 증권신고서(합병)
    ("extrRs", "01042429", "20260930"),       # 증권신고서(교환·이전)
    ("dvRs", "00258801", "20260821"),         # 증권신고서(분할)
    ("bdRs", "00132868", "20261016"),         # 증권신고서(채무증권)
]
PRICE = [
    "https://m.stock.naver.com/api/stock/123010/basic",
    "https://m.stock.naver.com/api/stock/123010/integration",
    "https://fchart.stock.naver.com/sise.nhn?symbol=123010&timeframe=day&count=3&requestType=0",
    "https://polling.finance.naver.com/api/realtime/domestic/stock/123010",
]

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")

out = {}
for api, cc, dt in SAMPLES:
    bgn = (datetime.strptime(dt, "%Y%m%d") - timedelta(days=120)).strftime("%Y%m%d")
    q = urllib.parse.urlencode({"crtfc_key": KEY, "corp_code": cc, "bgn_de": bgn, "end_de": dt})
    try:
        d = json.loads(get(f"https://opendart.fss.or.kr/api/{api}.json?{q}"))
        if isinstance(d.get("list"), list):
            d["list"] = d["list"][-2:]
        if isinstance(d.get("group"), list):
            for g in d["group"]:
                if isinstance(g.get("list"), list):
                    g["list"] = g["list"][-2:]
        out[f"{api}:{cc}"] = d
    except Exception as e:
        out[f"{api}:{cc}"] = {"error": str(e)}
for u in PRICE:
    try:
        out[u] = get(u)[:3000]
    except Exception as e:
        out[u] = {"error": str(e)}
os.makedirs("probe", exist_ok=True)
json.dump(out, open("probe/out.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("ok", len(out))
