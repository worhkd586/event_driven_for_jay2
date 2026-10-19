"""일회성 조사: 공개매수가·매수청구가·발행가가 원문에 어떻게 적혀 있는지 문맥을 모은다."""
import io, json, os, re, urllib.request, zipfile

KEY = os.environ["DART_API_KEY"]
DOCS = {
    "에스원 정정": ("20261016000452", r"공개매수\s*가격|1주당|매수가격"),
    "에스원 최초": ("20261015000001", r"공개매수\s*가격|1주당"),
    "한화머시너리": ("20261012000001", r"공개매수\s*가격|1주당|매수가격"),
    "BGF에코": ("20261008000001", r"공개매수\s*가격|1주당|매수가격"),
    "아리바이오홀딩스": ("20260929000584", r"매수\s*(?:예정\s*)?가격|청구\s*가격"),
    "금양 분할": ("20261012000401", r"매수\s*(?:예정\s*)?가격|청구\s*가격|매수청구"),
    "현대모비스 분할": (None, None),
    "이렘 신고서": ("20261008000305", r"보통주\s*[\d,]{3,}|발행가액|모집가액"),
    "시지트로닉스 주요": ("20261016000204", r"발행가|발행가액"),
    "다보링크 주요": ("20261012000396", r"발행가|발행가액"),
    "금양 유증": ("20260930000569", r"발행가|발행가액|증자방식"),
    "스코넥": ("20261002000483", r"발행가|발행가액|증자방식"),
}

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
for name, (r, pat) in DOCS.items():
    if not r:
        continue
    try:
        p = text(r)
    except Exception as e:
        out[name] = str(e); continue
    hits = [p[max(0, m.start() - 80): m.end() + 220] for m in re.finditer(pat, p)]
    out[name] = {"len": len(p), "head": p[:1500], "hits": hits[:40]}
json.dump(out, open("probe/out.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("ok")
