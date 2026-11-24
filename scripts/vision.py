# -*- coding: utf-8 -*-
"""Screen-pixel vision helpers for fully automatic UI element locating.

No third-party deps. Works on the raw BGRA buffer returned by desktop_ctl.grab().
Provides:
  find_color_regions()  connected-component detection for a color predicate
  red_buttons()         red button/label regions (bottom bars, dialog buttons)
  gold_regions()        gold highlight borders / selected card frames
  green_checks()        green check-mark blobs (name field validation)
  analyze()             dump all of the above for the current screen
"""
import desktop_ctl as d

MIN_REGION_PX = 40       # ignore tiny specks
STEP = 3                 # sampling stride (speed vs precision)


def _classify(r, g, b, kind):
    if kind == "red":
        # WoW button red: strong R, almost no G/B (e.g. 98,2,1)
        return r > 70 and g < 70 and b < 70 and r > g * 1.6 and r > b * 1.6
    if kind == "gold":
        # gold highlight border (e.g. 190,159,2 / 150,123,5)
        return r > 120 and g > 90 and b < 90 and r > b * 1.8 and g > b * 1.5
    if kind == "green":
        # green check mark
        return g > 90 and g > r * 1.3 and g > b * 1.25
    if kind == "bright":
        lum = (r + g + b) / 3.0
        return lum > 170
    return False


def find_color_regions(kind, step=STEP, min_px=MIN_REGION_PX,
                       xrange=None, yrange=None, px=None):
    """Return list of dicts {box:[x0,y0,x1,y1],cx,cy,area} for a color kind."""
    w, h, buf = px if px else d.grab(force=True)
    xlo, xhi = xrange or (0, w)
    ylo, yhi = yrange or (0, h)
    cols = (xhi - xlo + step - 1) // step
    rows = (yhi - ylo + step - 1) // step
    mask = bytearray(cols * rows)
    for iy in range(rows):
        y = ylo + iy * step
        base = y * w * 4
        for ix in range(cols):
            x = xlo + ix * step
            o = base + x * 4
            if _classify(buf[o + 2], buf[o + 1], buf[o], kind):
                mask[iy * cols + ix] = 1

    seen = bytearray(cols * rows)
    out = []
    for iy in range(rows):
        for ix in range(cols):
            idx = iy * cols + ix
            if not mask[idx] or seen[idx]:
                continue
            stack = [idx]
            seen[idx] = 1
            minx = maxx = ix
            miny = maxy = iy
            cnt = 0
            while stack:
                cur = stack.pop()
                cy, cx = divmod(cur, cols)
                cnt += 1
                if cx < minx: minx = cx
                if cx > maxx: maxx = cx
                if cy < miny: miny = cy
                if cy > maxy: maxy = cy
                for nx, ny in ((cx-1,cy),(cx+1,cy),(cx,cy-1),(cx,cy+1)):
                    if 0 <= nx < cols and 0 <= ny < rows:
                        nidx = ny * cols + nx
                        if mask[nidx] and not seen[nidx]:
                            seen[nidx] = 1
                            stack.append(nidx)
            if cnt * step * step >= min_px:
                x0 = xlo + minx * step
                y0 = ylo + miny * step
                x1 = min(xhi, xlo + (maxx + 1) * step)
                y1 = min(yhi, ylo + (maxy + 1) * step)
                out.append({"box": [x0, y0, x1, y1],
                            "cx": (x0 + x1) // 2, "cy": (y0 + y1) // 2,
                            "area": cnt * step * step})
    out.sort(key=lambda r: -r["area"])
    return out


def red_buttons(**kw):
    return find_color_regions("red", **kw)


def gold_regions(**kw):
    return find_color_regions("gold", **kw)


def green_checks(**kw):
    return find_color_regions("green", **kw)


def analyze(step=STEP):
    w, h, px = d.grab(force=True)
    print("screen %dx%d" % (w, h))
    for kind in ("red", "gold", "green"):
        rs = find_color_regions(kind, step=step, px=(w, h, px))
        print("\n[%s] %d regions" % (kind, len(rs)))
        for r in rs[:12]:
            print("  box=%s center=(%d,%d) area=%d" %
                  (r["box"], r["cx"], r["cy"], r["area"]))
    return w, h, px


if __name__ == "__main__":
    analyze()
