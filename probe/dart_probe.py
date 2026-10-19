"""일회성 조사: 유상증자 증권신고서 원문에서 신주인수권증서 매매기간이 어떻게 적혀 있는지 확인."""
import io, json, os, re, urllib.request, zipfile

KEY = os.environ["DART_API_KEY"]
DOCS = {"에코프로비엠 최초": "20260630000676", "에코프로비엠 0807 정정": "20260807000567",
        "삼성바이오 최초": "20260828001676", "큐리언트 0929 정정": "20260929000233"}
DATE = r"20\d{2}\s*[년.\-/]\s*\d{1,2}\s*[월.\-/]\s*\d{1,2}"

def text(r):
    with urllib.request.urlopen(f"https://opendart.fss.or.kr/api/document.xml?crtfc_key={KEY}&rcept_no={r}", timeout=60) as f:
        z = zipfile.ZipFile(io.BytesIO(f.read()))
    t = ""
    for n in z.namelist():
        raw = z.read(n)
        for enc in ("utf-8", "euc-kr", "cp949"):
            try:
                t += raw.decode(enc); break
            except UnicodeDecodeError:
                continue
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))

out = {}
for name, r in DOCS.items():
    try:
        p = text(r)
    except Exception as e:
        out[name] = str(e); continue
    hits = []
    for m in re.finditer(r"신주인수권\s*증서", p):
        seg = p[max(0, m.start() - 60): m.end() + 160]
        if re.search(DATE, seg):
            hits.append(seg)
    # 일정표 근처 (매매/상장 단어와 날짜가 함께 있는 구간)
    sched = [p[max(0, m.start() - 80): m.end() + 200] for m in re.finditer(r"(?:증서|권리)\s*(?:매매|상장|거래)", p)][:8]
    out[name] = {"len": len(p), "near_cert_with_date": hits[:10], "sched_like": sched}
json.dump(out, open("probe/out.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("ok")
