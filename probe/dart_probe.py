"""일회성 조사: 증권신고서에서 일반공모(실권주) 청약일 표현."""
import io, json, os, re, urllib.request, zipfile

KEY = os.environ["DART_API_KEY"]
DOCS = {"뷰노": "20261015000309", "에코프로비엠": "20261013000214", "휴림에이텍": "20261021000435"}
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
    for m in re.finditer(r"일반\s*공모\s*(?:청약|공고)|실권주\s*일반공모|구주주\s*청약|우리사주", p):
        seg = p[max(0, m.start() - 80): m.end() + 200]
        if re.search(DATE, seg):
            hits.append(seg)
    out[name] = hits[:25]
json.dump(out, open("probe/out.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("ok")
