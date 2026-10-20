"""일회성 조사: 최근 3일 현대위아 공시와 영업양수도 관련 공시명."""
import json, os, urllib.request, urllib.parse

KEY = os.environ["DART_API_KEY"]
def call(params):
    q = urllib.parse.urlencode(dict(params, crtfc_key=KEY))
    with urllib.request.urlopen(f"https://opendart.fss.or.kr/api/list.json?{q}", timeout=40) as r:
        return json.load(r)
out = {"wia": [], "transfer_like": [], "counts": {}}
for ty in ("A", "B", "C", "D", "E", "I"):
    page, n = 1, 0
    while True:
        d = call({"bgn_de": "20261018", "end_de": "20261020", "pblntf_ty": ty, "page_no": page, "page_count": 100})
        if d.get("status") != "000":
            out["counts"][ty] = d.get("status"); break
        for x in d["list"]:
            n += 1
            nm = x["report_nm"]
            if "위아" in x["corp_name"]:
                out["wia"].append([ty, x["corp_name"], x["corp_cls"], nm, x["rcept_no"], x.get("rm")])
            if any(w in nm for w in ("영업양", "양수도", "자산양", "사업양")):
                out["transfer_like"].append([ty, x["corp_name"], x["corp_cls"], nm, x["rcept_no"]])
        if page >= int(d.get("total_page", 1)):
            out["counts"][ty] = n; break
        page += 1
json.dump(out, open("probe/out.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("ok")
