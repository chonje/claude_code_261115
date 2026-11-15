#!/usr/bin/env python3
"""plan.json + config.json -> layouts.json -> (검증) -> index.html 에 데이터 삽입.

사용법:  python3 tools/build.py
표준 라이브러리만 사용. 배치 좌표는 모두 mm 정수, 벽 중심선 기준.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAN = json.loads((ROOT / "plan.json").read_text(encoding="utf-8"))
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
R = CFG["rules"]

MAIN = next(u for u in PLAN["usable"] if u["id"] == "main")
EXT = next(u for u in PLAN["usable"] if u["id"] == "ext")
W, H_MAIN = MAIN["w"], MAIN["h"]            # 19900, 7600
EXT_W, H_ALL = EXT["w"], EXT["y"] + EXT["h"]  # 10500, 10200
TOILET = PLAN["existing"]["toilet"]
DOOR = PLAN["mainDoor"]
DRAIN = PLAN["drainRef"]

LABELS = {"laundry": "세탁", "entrance": "현관", "kitchen": "주방", "lounge": "휴게",
          "storage": "창고", "office": "관리"}


def splits(x0, x1, n):
    """[x0, x1] 을 n 등분한 경계 (mm 정수)."""
    return [round(x0 + (x1 - x0) * i / n) for i in range(n + 1)]


def overlap(a0, a1, b0, b1):
    lo, hi = max(a0, b0), min(a1, b1)
    return (lo, hi) if hi > lo else None


def place_window(lo, hi, cols):
    """벽 구간 [lo, hi] 안에서 기둥을 피해 창 위치를 정한다. (a, b, reduced) 또는 None."""
    m = R["windowJambMarginMm"]
    lo, hi = lo + m, hi - m
    half = PLAN["columnSizeMm"] / 2 + R["columnClearanceMm"]
    free = [(lo, hi)]
    for c in sorted(cols):
        nxt = []
        for a, b in free:
            if c + half <= a or c - half >= b:
                nxt.append((a, b))
            else:
                if c - half > a:
                    nxt.append((a, c - half))
                if c + half < b:
                    nxt.append((c + half, b))
        free = nxt
    want, mid = R["windowWidthMm"], (lo + hi) / 2
    full = [(a, b) for a, b in free if b - a >= want]
    if full:
        a, b = min(full, key=lambda s: abs((s[0] + s[1]) / 2 - mid))
        s = min(max(mid - want / 2, a), b - want)
        return round(s), round(s + want), False
    if not free:
        return None
    a, b = max(free, key=lambda s: s[1] - s[0])
    if b - a < R["windowMinWidthMm"]:
        return None
    return round(a), round(b), True


def windows_for(sp):
    """방이 접한 외벽마다 창 1개."""
    out = []
    x0, y0, x1, y1 = sp["x"], sp["y"], sp["x"] + sp["w"], sp["y"] + sp["h"]
    for wall in PLAN["exteriorWalls"]:
        horiz = wall["y1"] == wall["y2"]
        if horiz:
            if wall["y1"] not in (y0, y1):
                continue
            ov = overlap(x0, x1, wall["x1"], wall["x2"])
            cols = [c["x"] for c in PLAN["columns"] if abs(c["y"] - wall["y1"]) < 300]
        else:
            if wall["x1"] not in (x0, x1):
                continue
            ov = overlap(y0, y1, wall["y1"], wall["y2"])
            cols = [c["y"] for c in PLAN["columns"] if abs(c["x"] - wall["x1"]) < 300]
        if not ov:
            continue
        w = place_window(ov[0], ov[1], cols)
        if not w:
            continue
        a, b, reduced = w
        seg = ({"x1": a, "y1": wall["y1"], "x2": b, "y2": wall["y1"]} if horiz
               else {"x1": wall["x1"], "y1": a, "x2": wall["x1"], "y2": b})
        out.append({"wall": wall["id"], "reduced": reduced, **seg})
    return out


def make_room(n, x0, x1, y0, y1, corridor_edge, bath_side):
    """corridor_edge: 'bottom'|'top' (복도와 맞닿는 변). bath_side: 'left'|'right'."""
    bw, bd, dw = R["bathWidthMm"], R["bathDepthMm"], R["doorWidthMm"]
    bx = x0 if bath_side == "left" else x1 - bw
    by = y1 - bd if corridor_edge == "bottom" else y0
    fx0, fx1 = (x0 + bw, x1) if bath_side == "left" else (x0, x1 - bw)  # 문을 낼 수 있는 구간
    dx0 = round((fx0 + fx1) / 2 - dw / 2)
    dy = y1 if corridor_edge == "bottom" else y0
    hinge_near_bath = bath_side == "left"
    door = {"x1": dx0, "y1": dy, "x2": dx0 + dw, "y2": dy,
            "into": [0, -1] if corridor_edge == "bottom" else [0, 1],
            "hinge": "start" if hinge_near_bath else "end"}
    sp = {"id": f"R{n}", "type": "room", "label": str(n), "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0,
          "bath": {"x": bx, "y": by, "w": bw, "h": bd}, "door": door}
    sp["windows"] = windows_for(sp)
    sp["walls"] = sorted({w["wall"] for w in sp["windows"]})
    cx, cy = bx + bw / 2, by + bd / 2
    sp["pipeMm"] = round(abs(cx - DRAIN["x"]) + abs(cy - DRAIN["y"]))
    return sp


def common(kind, x0, x1, y0, y1, corridor_edge, door=True):
    sp = {"id": kind, "type": kind, "label": LABELS[kind], "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}
    if door:
        dw = R["doorWidthMm"]
        dx0 = round((x0 + x1) / 2 - dw / 2)
        dy = y0 if corridor_edge == "top" else y1
        sp["door"] = {"x1": dx0, "y1": dy, "x2": dx0 + dw, "y2": dy,
                      "into": [0, 1] if corridor_edge == "top" else [0, -1], "hinge": "start"}
    return sp


def dim(x1, y1, x2, y2):
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "mm": abs(x2 - x1) + abs(y2 - y1)}


def build_layout(key, title, summary, scenario):
    p = CFG["layouts"][key]
    d = p["topDepthMm"]
    cw = R["corridorDoubleMinMm"]
    c1 = d + cw  # 복도 아래쪽 선
    spaces, n = [], 0

    xs = splits(0, W, p["topRooms"])
    for i in range(p["topRooms"]):
        n += 1
        spaces.append(make_room(n, xs[i], xs[i + 1], 0, d, "bottom", "right" if i % 2 == 0 else "left"))

    spaces.append({"id": "corridor", "type": "corridor", "label": "복도", "x": 0, "y": d, "w": W, "h": cw})

    bxs = []
    if p.get("bottomRooms"):
        bxs = splits(0, EXT_W, p["bottomRooms"])
        for i in range(p["bottomRooms"]):
            n += 1
            spaces.append(make_room(n, bxs[i], bxs[i + 1], c1, H_ALL, "top", "right" if i % 2 == 0 else "left"))
    else:  # C안: 왼쪽 아래는 공용
        spaces.append(common("lounge", 0, 4500, c1, H_ALL, "top"))
        spaces.append(common("kitchen", 4500, 8000, c1, H_ALL, "top"))
        spaces.append(common("laundry", 8000, EXT_W, c1, H_ALL, "top"))
        bxs = [0, 4500, 8000, EXT_W]

    tx1 = TOILET["x"] + TOILET["w"]  # 12800
    ex1 = 14900
    if p.get("bottomRooms"):
        spaces.append(common("laundry", EXT_W, tx1, c1, H_MAIN, "top"))
        spaces.append(common("entrance", tx1, ex1, c1, H_MAIN, "top", door=False))
        spaces.append(common("kitchen", ex1, 17400, c1, H_MAIN, "top"))
        spaces.append(common("lounge", 17400, W, c1, H_MAIN, "top"))
    else:
        spaces.append(common("storage", EXT_W, tx1, c1, H_MAIN, "top"))
        spaces.append(common("entrance", tx1, ex1, c1, H_MAIN, "top", door=False))
        spaces.append(common("office", ex1, W, c1, H_MAIN, "top"))

    dw, bal = R["doorWidthMm"], CFG["balcony"]["depthMm"]
    ey = round(d + cw / 2 - dw / 2)
    exit_ = {"wall": "left", "x1": 0, "y1": ey, "x2": 0, "y2": ey + dw,
             "balcony": {"x": -bal, "y": d, "w": bal, "h": cw, "estimated": True}}

    for sp in spaces:
        sp["areaM2"] = round(sp["w"] * sp["h"] / 1e6, 2)

    dims = [dim(xs[i], -700, xs[i + 1], -700) for i in range(len(xs) - 1)]
    dims.append(dim(0, -1500, W, -1500))
    dims += [dim(bxs[i], H_ALL + 700, bxs[i + 1], H_ALL + 700) for i in range(len(bxs) - 1)]
    dims += [dim(W + 700, 0, W + 700, d), dim(W + 700, d, W + 700, c1), dim(W + 700, c1, W + 700, H_MAIN)]
    dims += [dim(-2000, 0, -2000, d), dim(-2000, d, -2000, c1), dim(-2000, c1, -2000, H_ALL)]
    dims.append(dim(-2800, 0, -2800, H_ALL))

    rooms = [s for s in spaces if s["type"] == "room"]
    usable = sum(u["w"] * u["h"] for u in PLAN["usable"]) / 1e6
    room_area = sum(s["areaM2"] for s in rooms)
    notes = []
    red = [s["label"] for s in rooms if any(w["reduced"] for w in s["windows"])]
    if red:
        notes.append(f"{', '.join(red)}번 방은 외벽 기둥 때문에 창 폭이 {R['windowWidthMm']:,}mm보다 좁아짐 (기둥 실제 크기 확인 필요)")
    return {
        "key": key, "title": title, "summary": summary, "windowScenario": scenario,
        "spaces": spaces, "exit": exit_, "dims": dims, "notes": notes,
        "stats": {
            "rooms": len(rooms),
            "avgRoomM2": round(room_area / len(rooms), 2),
            "minRoomM2": min(s["areaM2"] for s in rooms),
            "roomAreaM2": round(room_area, 2),
            "roomRatio": round(room_area / usable, 4),
            "corridorM2": round(sum(s["areaM2"] for s in spaces if s["type"] == "corridor"), 2),
            "commonM2": round(sum(s["areaM2"] for s in spaces if s["type"] not in ("room", "corridor")), 2),
            "totalPipeM": round(sum(s["pipeMm"] for s in rooms) / 1000, 1),
            "usableM2": round(usable, 2),
        },
    }


def main():
    all4 = {"top": True, "bottom": True, "left": True, "right": True}
    top_only = {"top": True, "bottom": False, "left": False, "right": False}
    layouts = [
        build_layout("A", "A안 · 11실", "위쪽 벽 7실 + 왼쪽 아래 4실. 가운데 직선 복도, 계단 옆에 세탁·현관·주방·휴게.", all4),
        build_layout("B", "B안 · 실 수 최대", "최소 면적 9㎡에 맞춰 방 폭을 줄여 위쪽 9실 + 왼쪽 아래 5실.", all4),
        build_layout("C", "C안 · 위쪽 벽만 창", "위쪽 벽에만 창을 낼 수 있을 때. 방은 위쪽 9실뿐이고 나머지는 공용.", top_only),
    ]
    (ROOT / "layouts.json").write_text(json.dumps(layouts, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("layouts.json 생성:", ", ".join(f"{l['key']}={l['stats']['rooms']}실" for l in layouts))

    rc = subprocess.call([sys.executable, str(ROOT / "tools" / "validate.py")])
    if rc != 0:
        print("검증 실패: index.html 은 갱신하지 않음")
        return rc

    data = json.dumps({"plan": PLAN, "config": CFG, "layouts": layouts}, ensure_ascii=False, separators=(",", ":"))
    data = data.replace("</", "<\\/")
    html_path = ROOT / "index.html"
    html = html_path.read_text(encoding="utf-8")
    s, e = "/*DATA:START*/", "/*DATA:END*/"
    i, j = html.index(s), html.index(e)
    html = html[: i + len(s)] + data + html[j:]
    html_path.write_text(html, encoding="utf-8")
    print("index.html 데이터 갱신 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
