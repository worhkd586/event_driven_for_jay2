"""data/view.json을 이벤트 북 페이지 db용 문서로 나눈다.

사용: python collector/artifact_docs.py <출력 디렉터리>
결과: <출력>/meta.json   (갱신 시각, 시세, 조각 수)
      <출력>/c-N.json    (이벤트 묶음 조각, 문서당 256KiB 제한 안쪽)
모두 컬렉션 "view"의 문서다.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "view.json")
LIMIT = 200 * 1024


def dump(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def main(out):
    with open(SRC, encoding="utf-8") as f:
        view = json.load(f)
    chunks, cur, size = [], [], 0
    for g in view["groups"]:
        n = len(dump(g).encode())
        if cur and size + n > LIMIT:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(g)
        size += n
    if cur:
        chunks.append(cur)
    os.makedirs(out, exist_ok=True)
    for name in os.listdir(out):
        if name.endswith(".json"):
            os.remove(os.path.join(out, name))
    meta = {"updated_at": view["updated_at"], "events_updated_at": view.get("events_updated_at"),
            "today": view.get("today"), "prices": view.get("prices", {}), "chunks": len(chunks)}
    with open(os.path.join(out, "meta.json"), "w", encoding="utf-8") as f:
        f.write(dump(meta))
    print(f"meta: {len(dump(meta).encode()) // 1024}KB, 시세 {len(meta['prices'])}종목")
    for i, c in enumerate(chunks):
        body = dump({"groups": c})
        with open(os.path.join(out, f"c-{i}.json"), "w", encoding="utf-8") as f:
            f.write(body)
        print(f"c-{i}: {len(c)}건, {len(body.encode()) // 1024}KB")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "artifact_docs")
