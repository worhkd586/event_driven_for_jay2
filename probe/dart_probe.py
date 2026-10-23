"""일회성 조사: 최근 90일 철회·취소·중단·해지 관련 공시명 (B, C, D, I)."""
import json, os, re, urllib.request, urllib.parse
from datetime import datetime, timedelta

KEY = os.environ["DART_API_KEY"]
def call(params):
    q = urllib.parse.urlencode(dict(params, crtfc_key=KEY))
    for i in range(4):
        try:
            with urllib.request.urlopen(f"https://opendart.fss.or.kr/api/list.json?{q}", timeout=60) as r:
                return json.load(r)
        except Exception as e:
            err = e
    return {"status": "err", "message": str(err)}
end = datetime.now() + timedelta(hours=9)
bgn = end - timedelta(days=60)
out = {"names": {}, "rows": []}
for ty in ("B", "C", "D", "I"):
    page = 1
    while True:
        d = call({"bgn_de": bgn.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"), "pblntf_ty": ty, "page_no": page, "page_count": 100})
        if d.get("status") != "000":
            break
        for x in d["list"]:
            nm = re.sub(r"\s+", "", x["report_nm"])
            if x.get("corp_cls") not in ("Y", "K"):
                continue
            if re.search(r"철회|취소|중단|해지|해제|무산|중지", nm) and re.search(r"증자|합병|분할|교환|이전|양수|양도|공개매수|사채|신고서|발행", nm):
                key = ty + ":" + re.sub(r"^\[[^\]]*\]", "", nm)
                out["names"][key] = out["names"].get(key, 0) + 1
                out["rows"].append([ty, x["corp_name"], x["corp_code"], nm, x["rcept_no"]])
        if page >= int(d.get("total_page", 1)):
            break
        page += 1
out["names"] = dict(sorted(out["names"].items(), key=lambda kv: -kv[1]))
json.dump(out, open("probe/out.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("ok")
