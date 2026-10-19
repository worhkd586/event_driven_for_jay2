"""일회성 조사: 유상증자 확정발행가를 [발행조건확정] 증권신고서 원문에서 읽을 수 있는지 확인."""
import io, json, os, re, urllib.parse, urllib.request, zipfile

KEY = os.environ["DART_API_KEY"]
CORPS = [("01160363", "에코프로비엠"), ("01042650", "큐리언트"), ("00877059", "삼성바이오로직스")]

def get(url, raw=False):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as r:
        b = r.read()
        return b if raw else b.decode("utf-8", "replace")

def api(path, **p):
    p["crtfc_key"] = KEY
    return json.loads(get(f"https://opendart.fss.or.kr/api/{path}.json?" + urllib.parse.urlencode(p)))

out = {}
for cc, name in CORPS:
    o = out[name] = {}
    o["estk_status_builder_range"] = {k: api("estkRs", corp_code=cc, bgn_de=b, end_de=e).get("status") for k, (b, e) in
        {"0704-1019": ("20260704", "20261019"), "0815-1019": ("20260815", "20261019"), "0615-1013": ("20260615", "20261013")}.items()}
    lst = api("list", corp_code=cc, bgn_de="20260701", end_de="20261019", pblntf_ty="C", page_count=100)
    filings = [(x["rcept_no"], x["report_nm"]) for x in lst.get("list", [])]
    o["C_filings"] = filings
    target = next((r for r, n in filings if "발행조건확정" in n), None) or next((r for r, n in filings if "증권신고서(지분증권)" in n), None)
    o["doc_rcept"] = target
    if not target:
        continue
    z = zipfile.ZipFile(io.BytesIO(get(f"https://opendart.fss.or.kr/api/document.xml?crtfc_key={KEY}&rcept_no={target}", raw=True)))
    text = ""
    for n in z.namelist():
        raw = z.read(n)
        for enc in ("utf-8", "euc-kr", "cp949"):
            try:
                text += raw.decode(enc); break
            except UnicodeDecodeError:
                continue
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))
    o["doc_len"] = len(plain)
    snips = []
    for pat in [r"확정\s*발행가", r"확정\s*모집가", r"1주당\s*(?:확정\s*)?(?:모집|발행)가", r"모집가액"]:
        for m in list(re.finditer(pat, plain))[:3]:
            snips.append(pat + " :: " + plain[max(0, m.start() - 120): m.end() + 200])
    o["snips"] = snips[:12]
json.dump(out, open("probe/out.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("ok")
