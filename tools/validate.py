#!/usr/bin/env python3
"""layouts.json 을 plan.json / config.json 규칙으로 검증한다. (생성기와 독립적으로 좌표만 보고 판정)

사용법:  python3 tools/validate.py            # 실패가 있으면 종료 코드 1
         python3 tools/validate.py --selftest # 일부러 망가뜨린 배치를 잡아내는지 확인
"""
import copy
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAN = json.loads((ROOT / "plan.json").read_text(encoding="utf-8"))
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
R = CFG["rules"]
OCCUPIABLE = {"room", "laundry", "kitchen", "lounge", "office"}


def box(s):
    return s["x"], s["y"], s["x"] + s["w"], s["y"] + s["h"]


def inter_area(a, b):
    ax0, ay0, ax1, ay1 = box(a)
    bx0, by0, bx1, by1 = box(b)
    w, h = min(ax1, bx1) - max(ax0, bx0), min(ay1, by1) - max(ay0, by0)
    return w * h if w > 0 and h > 0 else 0


def shared_edge(a, b):
    """두 직사각형이 맞닿은 변의 길이(mm)와 방향. 겹치거나 떨어져 있으면 0."""
    ax0, ay0, ax1, ay1 = box(a)
    bx0, by0, bx1, by1 = box(b)
    if ay1 == by0 or by1 == ay0:
        return max(0, min(ax1, bx1) - max(ax0, bx0)), "h"
    if ax1 == bx0 or bx1 == ax0:
        return max(0, min(ay1, by1) - max(ay0, by0)), "v"
    return 0, None


def wall_touch(s, wall):
    x0, y0, x1, y1 = box(s)
    if wall["y1"] == wall["y2"]:
        if wall["y1"] not in (y0, y1):
            return 0
        return max(0, min(x1, wall["x2"]) - max(x0, wall["x1"]))
    if wall["x1"] not in (x0, x1):
        return 0
    return max(0, min(y1, wall["y2"]) - max(y0, wall["y1"]))


def seg_on_shared_edge(seg, a, b):
    """문 선분이 a 와 b 가 맞닿은 변 위에 완전히 놓여 있는가."""
    ax0, ay0, ax1, ay1 = box(a)
    bx0, by0, bx1, by1 = box(b)
    if seg["y1"] == seg["y2"]:
        y = seg["y1"]
        if not ((y == ay0 and y == by1) or (y == ay1 and y == by0)):
            return False
        return max(ax0, bx0) <= seg["x1"] and seg["x2"] <= min(ax1, bx1)
    x = seg["x1"]
    if not ((x == ax0 and x == bx1) or (x == ax1 and x == bx0)):
        return False
    return max(ay0, by0) <= seg["y1"] and seg["y2"] <= min(ay1, by1)


def validate(layout):
    errs = []
    sp = layout["spaces"]
    rooms = [s for s in sp if s["type"] == "room"]
    corridors = [s for s in sp if s["type"] == "corridor"]
    scenario = layout["windowScenario"]
    walls = {w["id"]: w for w in PLAN["exteriorWalls"]}

    # 1. 평면 안에 있고 서로 겹치지 않음
    usable = list(PLAN["usable"])
    if layout.get("toiletConverted"):  # 기존 화장실을 세탁실로 쓰는 경우 그 칸도 배치 대상
        usable.append(PLAN["existing"]["toilet"])
    for s in sp:
        inside = sum(inter_area(s, u) for u in usable)
        if s["w"] <= 0 or s["h"] <= 0 or inside != s["w"] * s["h"]:
            errs.append(f"{s['id']}: 평면 밖으로 나감")
    for i, a in enumerate(sp):
        for b in sp[i + 1:]:
            if inter_area(a, b) > 0:
                errs.append(f"{a['id']} ↔ {b['id']}: 겹침")

    # 2. 면적 합
    total = sum(s["w"] * s["h"] for s in sp) / 1e6
    target = PLAN["usableAreaM2"]
    if layout.get("toiletConverted"):
        t = PLAN["existing"]["toilet"]
        target = round(target + t["w"] * t["h"] / 1e6, 2)
    if abs(total - target) / target > R["areaTolerance"]:
        errs.append(f"면적 합 {total:.2f}㎡ 가 {target}㎡ ±{R['areaTolerance']:.0%} 를 벗어남")

    # 3. 방: 최소 면적·폭, 복도 접함 + 문, 창 가능 외벽, 욕실
    for r in rooms:
        area = r["w"] * r["h"] / 1e6
        if area < R["roomMinAreaM2"]:
            errs.append(f"{r['id']}: 면적 {area:.2f}㎡ < {R['roomMinAreaM2']}㎡")
        if min(r["w"], r["h"]) < R["roomMinWidthMm"]:
            errs.append(f"{r['id']}: 폭 {min(r['w'], r['h'])}mm < {R['roomMinWidthMm']}mm")
        if not any(shared_edge(r, c)[0] >= R["doorWidthMm"] for c in corridors):
            errs.append(f"{r['id']}: 복도에 접하지 않음")
        d = r.get("door")
        if not d or not any(seg_on_shared_edge(d, r, c) for c in corridors):
            errs.append(f"{r['id']}: 문이 복도 쪽 변 위에 없음")
        elif abs(d["x2"] - d["x1"]) + abs(d["y2"] - d["y1"]) < R["doorWidthMm"]:
            errs.append(f"{r['id']}: 문 폭 부족")
        ok_wall = [wid for wid, w in walls.items()
                   if scenario.get(wid) and wall_touch(r, w) >= R["windowMinWidthMm"]]
        ok_win = [w for w in r.get("windows", [])
                  if scenario.get(w["wall"])
                  and abs(w["x2"] - w["x1"]) + abs(w["y2"] - w["y1"]) >= R["windowMinWidthMm"]]
        if not ok_wall or not ok_win:
            errs.append(f"{r['id']}: 창 가능 외벽에 접하지 않음 (또는 기둥 때문에 창을 낼 자리 없음)")
        b = r.get("bath")
        if not b:
            errs.append(f"{r['id']}: 욕실 없음")
        else:
            if inter_area(b, r) != b["w"] * b["h"]:
                errs.append(f"{r['id']}: 욕실이 방 밖으로 나감")
            if sorted((b["w"], b["h"])) != sorted((R["bathWidthMm"], R["bathDepthMm"])):
                errs.append(f"{r['id']}: 욕실 크기가 규칙과 다름")
            if d:
                bx0, by0, bx1, by1 = box(b)
                if d["y1"] == d["y2"] and by0 <= d["y1"] <= by1 and min(d["x2"], bx1) - max(d["x1"], bx0) > 0:
                    errs.append(f"{r['id']}: 문이 욕실에 막힘")

    # 4. 복도 폭 (양쪽에 실이 있으면 double 기준)
    occ = OCCUPIABLE if R["commonCountsAsRoomForCorridor"] else {"room"}
    for c in corridors:
        horiz = c["w"] >= c["h"]
        width = c["h"] if horiz else c["w"]
        cx0, cy0, cx1, cy1 = box(c)
        sides = set()
        for s in sp:
            if s["type"] not in occ:
                continue
            ln, direction = shared_edge(s, c)
            if ln <= 0 or direction != ("h" if horiz else "v"):
                continue
            sx0, sy0, sx1, sy1 = box(s)
            sides.add(("lo" if sy1 == cy0 else "hi") if horiz else ("lo" if sx1 == cx0 else "hi"))
        need = R["corridorDoubleMinMm"] if len(sides) == 2 else R["corridorSingleMinMm"]
        if width < need:
            errs.append(f"{c['id']}: 폭 {width}mm < {need}mm ({'양쪽' if len(sides) == 2 else '한쪽'} 기준)")

    # 5. 공용 공간: 필수 4종, 최소 면적, 복도 접함
    for kind, min_a in R["commonMinAreaM2"].items():
        found = [s for s in sp if s["type"] == kind]
        if not found:
            errs.append(f"필수 공용 공간 없음: {kind}")
        for s in found:
            if s["w"] * s["h"] / 1e6 < min_a:
                errs.append(f"{kind}: 면적 {s['w'] * s['h'] / 1e6:.2f}㎡ < {min_a}㎡")
    for s in sp:
        if s["type"] not in ("room", "corridor"):
            if any(shared_edge(s, c)[0] >= R["doorWidthMm"] for c in corridors):
                continue
            # 복도에 직접 닿지 않으면, 복도에 닿는 다른 공용 공간을 거쳐 문으로 들어가야 함
            via = next((v for v in sp if v["id"] == s.get("via")), None)
            ok = (via is not None and via["type"] not in ("room", "corridor")
                  and any(shared_edge(via, c)[0] >= R["doorWidthMm"] for c in corridors)
                  and s.get("door") and seg_on_shared_edge(s["door"], s, via))
            if not ok:
                errs.append(f"{s['id']}: 복도에 접하지 않음")

    # 6. 현관이 주출입구를 품고, 비상구는 복도 끝 외벽 + 주출입구 반대편
    md = PLAN["mainDoor"]
    for e in [s for s in sp if s["type"] == "entrance"]:
        x0, y0, x1, y1 = box(e)
        if not (md["y1"] in (y0, y1) and x0 <= md["x1"] and md["x2"] <= x1):
            errs.append("entrance: 주출입구가 현관 안에 들어오지 않음")
    ex = layout.get("exit")
    if not ex:
        errs.append("비상구 없음")
    else:
        w = walls.get(ex["wall"])
        on_wall = w and ex["x1"] == ex["x2"] == w["x1"] and w["y1"] <= ex["y1"] and ex["y2"] <= w["y2"]
        if not on_wall:
            errs.append("비상구가 외벽 위에 없음")
        if not any(box(c)[0] == ex["x1"] and box(c)[1] <= ex["y1"] and ex["y2"] <= box(c)[3] for c in corridors):
            errs.append("비상구가 복도 끝에 닿지 않음")
        if not ex.get("balcony"):
            errs.append("비상구 바깥 발코니 없음")
        ax, ay = (md["x1"] + md["x2"]) / 2, md["y1"]
        bx, by = ex["x1"], (ex["y1"] + ex["y2"]) / 2
        xs = [u["x"] for u in PLAN["usable"]] + [u["x"] + u["w"] for u in PLAN["usable"]]
        ys = [u["y"] for u in PLAN["usable"]] + [u["y"] + u["h"] for u in PLAN["usable"]]
        diag = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        if math.hypot(ax - bx, ay - by) < R["exitMinSeparationRatio"] * diag:
            errs.append("비상구가 주출입구와 충분히 떨어져 있지 않음")
    return errs, total


def selftest(layouts):
    """검증기가 실제로 실패를 잡아내는지 확인."""
    cases = []
    a = copy.deepcopy(layouts[0]); a["spaces"][0]["w"] += 500; cases.append(("방 겹침", a))
    a = copy.deepcopy(layouts[0]); a["spaces"][0]["y"] -= 300; cases.append(("평면 밖", a))
    a = copy.deepcopy(layouts[0]); a["spaces"] = a["spaces"][1:]; cases.append(("면적 합 부족", a))
    a = copy.deepcopy(layouts[0]); a["windowScenario"] = {"top": True}; cases.append(("창 불가 벽의 방", a))
    a = copy.deepcopy(layouts[0])
    c = next(s for s in a["spaces"] if s["type"] == "corridor"); c["h"] -= 200
    cases.append(("복도 폭 부족", a))
    ok = True
    for name, lay in cases:
        errs, _ = validate(lay)
        print(f"  selftest {name}: {'잡아냄' if errs else '놓침 ✗'}")
        ok = ok and bool(errs)
    return ok


def main():
    layouts = json.loads((ROOT / "layouts.json").read_text(encoding="utf-8"))
    if "--selftest" in sys.argv:
        return 0 if selftest(layouts) else 1
    failed = False
    for lay in layouts:
        errs, total = validate(lay)
        st = lay["stats"]
        print(f"[{lay['key']}] {st['rooms']}실 · 면적 합 {total:.2f}㎡ (기준 {st['usableM2']}㎡) · "
              f"최소 방 {st['minRoomM2']}㎡ → {'통과' if not errs else '실패'}")
        for e in errs:
            print("   ✗", e)
        failed = failed or bool(errs)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
