"""일회성 조사: DART 상세 API·증권신고서 API·시세 API의 실제 응답 형태를 probe/out.json에 저장한다."""
import json, os, urllib.parse, urllib.request
from datetime import datetime, timedelta

KEY = os.environ["DART_API_KEY"]
SAMPLES = [  # (api, corp_code, rcept_dt)
    ("piicDecsn", "01160363", "20261013"),    # 유상증자 에코프로비엠 (주주배정)
    ("estkRs", "01160363", "20261013"),       # 증권신고서(지분증권) 에코프로비엠
    ("estkRs", "01344202", "20261015"),       # 증권신고서(지분증권) 뷰노
    ("piicDecsn", "01344202", "20261015"),    # 유상증자 뷰노
    ("bdwtIsDecsn", "00317210", "20261013"),  # BW 성호전자
    ("bdRs", "00317210", "20261013"),         # 증권신고서(채무증권) 성호전자
    ("bdwtIsDecsn", "00131504", "20260811"),  # BW 서한
]
PRICE = [
    "https://polling.finance.naver.com/api/realtime/domestic/stock/247540,338220,115180",
    "https://m.stock.naver.com/api/stock/247540/integration",
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
            d["list"] = d["list"][-3:]
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
