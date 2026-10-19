"""data/dart_events.json을 이벤트 북 페이지 db용 월별 문서로 나눈다.

사용: python collector/artifact_docs.py <출력 디렉터리>
결과: <출력>/m-YYYYMM.json  (컬렉션 "dart"의 문서 하나씩, 문서당 256KiB 제한 안쪽)
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "dart_events.json")
LIMIT = 240 * 1024


def compact(e):
    return {"r": e["rcept_no"], "d": e["rcept_dt"], "t": e["type"], "n": e["corp_name"],
            "c": e["corp_code"], "s": e["stock_code"], "m": e["market"], "nm": e["report_nm"],
            "a": e.get("appraisal", "?"), "f": e.get("flags", []), "x": bool(e.get("amended"))}


def main(out):
    with open(SRC, encoding="utf-8") as f:
        store = json.load(f)
    months = {}
    for e in store["events"]:
        months.setdefault(e["rcept_dt"][:6], []).append(compact(e))
    os.makedirs(out, exist_ok=True)
    for m, evs in sorted(months.items()):
        doc = {"month": m, "updated_at": store["updated_at"], "events": evs}
        body = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
        if len(body.encode()) > LIMIT:
            sys.exit(f"m-{m}: {len(body.encode())} bytes, 문서 크기 제한 초과 (월을 더 잘게 나눠야 함)")
        with open(os.path.join(out, f"m-{m}.json"), "w", encoding="utf-8") as f:
            f.write(body)
        print(f"m-{m}: {len(evs)}건, {len(body.encode()) // 1024}KB")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "artifact_docs")
