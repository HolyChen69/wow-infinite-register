# -*- coding: utf-8 -*-
"""Interactive coordinate calibration on the REAL desktop.

Never hardcode screen coordinates — they break when the monitor, resolution
or UI scale changes. This tool captures coordinates by having the user
actually CLICK each UI element on the live screen, then builds a config.

Commands:
  capture --out draft.json [options]   guide user to click every button/element;
                                       write a draft config with generated
                                       detection boxes around each click point
  boxes   --config draft.json          guide user to DRAG-select each detection
                                       region (gold frame / halo / green check
                                       / red buttons) for precise verification
  probe   --config draft.json          verify all detection regions against the
                                       current screen state

Usage pattern:
  1. Bring the WoW window to the foreground and make sure it is unobstructed.
  2. Run `capture`, follow the on-screen prompts and click each element in
     order. The UI naturally advances (登录页 -> 规则集 -> 创建 -> 命名).
  3. Review the printed coordinate table; fix any wrong entry by re-running
     capture or editing the JSON directly.
  4. Optionally run `boxes` to drag-select precise detection regions.
  5. Run `probe` to confirm each detector reads True on the selected state.
"""
import argparse
import ctypes
import json
import sys
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
# DPI awareness MUST match desktop_ctl.py, otherwise GetCursorPos/ScreenMetrics
# return logical (scaled) coordinates that are 1.5x off from the physical
# coordinates register.py uses at runtime. See calibration.md.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass
user32.GetAsyncKeyState.argtypes = [wintypes.INT]
user32.GetAsyncKeyState.restype = wintypes.SHORT
VK_LBUTTON = 0x01


def get_cursor():
    p = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def _wait_release(timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if not (user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000):
            return True
        time.sleep(0.05)
    return False


def wait_click(timeout=300):
    """Wait for a left-button press; return (x,y) at press time."""
    print("    >> 请把鼠标移到目标上，单击左键 ...", flush=True)
    _wait_release()
    end = time.time() + timeout
    while time.time() < end:
        if user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000:
            pos = get_cursor()
            _wait_release()
            time.sleep(0.25)  # debounce
            print("    << 已记录 (%d, %d)" % pos, flush=True)
            return pos
        time.sleep(0.05)
    return None


def wait_drag(timeout=300):
    """Wait for a left-button drag; return [x0,y0,x1,y1]."""
    print("    >> 请按住左键从区域一角拖到对角后松开 ...", flush=True)
    _wait_release()
    end = time.time() + timeout
    while time.time() < end:
        if user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000:
            p0 = get_cursor()
            time.sleep(0.15)
            # wait for release while tracking
            while user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000:
                time.sleep(0.05)
            p1 = get_cursor()
            box = [min(p0[0], p1[0]), min(p0[1], p1[1]),
                   max(p0[0], p1[0]), max(p0[1], p1[1])]
            time.sleep(0.25)
            print("    << 已记录框 %s" % box, flush=True)
            return box
        time.sleep(0.05)
    return None


def _box_around(pt, dx0, dy0, dx1, dy1):
    x, y = pt
    return [x + dx0, y + dy0, x + dx1, y + dy1]


# Click steps in the natural UI order. The game advances as the user clicks.
CLICK_STEPS = [
    ("ui.reconnect",          "【登录页】点击『重新连接』按钮"),
    ("_ruleset",              "【规则集页】点击目标规则集卡片（如 PVP）"),
    ("ui.ruleset_confirm",    "【规则集页】点击『选择』按钮"),
    ("_gender_male",          "【创建页】点击『男性』图标"),
    ("_gender_female",        "【创建页】点击『女性』图标"),
    ("_race",                 "【创建页】点击目标种族头像"),
    ("_class",                "【创建页】点击目标职业图标"),
    ("ui.custom_appearance",  "【创建页】点击『自定外观』按钮"),
    ("ui.name_main",          "【命名页】点击主名输入框"),
    ("ui.name_sub",           "【命名页】点击副名输入框"),
    ("ui.finish",             "【命名页】点击『完成』按钮"),
    ("ui.dialog_confirm",     "【阵营弹窗】点击『确定』按钮（若未弹出，点屏幕右上角空白跳过）"),
    ("ui.back_1",             "【后退】在命名页/创建页点击『后退』按钮"),
    ("ui.back_3",             "【后退】在角色选择页点击『返回』按钮（位置更靠下）"),
]

BOX_STEPS = [
    ("ruleset",  "【规则集页】框住目标规则集卡片的金色左边框"),
    ("gender_L", "【创建页】框住男性图标"),
    ("gender_R", "【创建页】框住女性图标"),
    ("race",     "【创建页】框住目标种族头像的光晕区域"),
    ("klass",    "【创建页】框住目标职业图标"),
    ("green",    "【命名页】框住名字右侧的绿色对勾"),
    ("dialog",   "【阵营弹窗】框住红色『确定』按钮"),
    ("finish",   "【命名页】框住红色『完成』按钮"),
]


def cmd_capture(args):
    pts = {}
    print("=" * 60)
    print("坐标采集：请按顺序点击下列元素（共 %d 项）" % len(CLICK_STEPS))
    print("提示：先把魔兽世界窗口切到前台并完全可见。")
    print("      点击后界面会自动前进，按提示继续即可。")
    print("      若某项点错，可在生成后直接编辑 JSON 修正。")
    print("=" * 60)
    for key, prompt in CLICK_STEPS:
        print("\n[%02d/%02d] %s" % (CLICK_STEPS.index((key, prompt)) + 1,
                                    len(CLICK_STEPS), prompt), flush=True)
        pos = wait_click()
        if pos is None:
            print("超时未采集，跳过该项", flush=True)
            continue
        pts[key] = list(pos)

    for k in ("ui.reconnect", "ui.ruleset_confirm", "ui.custom_appearance",
              "ui.name_main", "ui.name_sub", "ui.finish",
              "ui.dialog_confirm", "ui.back_1", "ui.back_3"):
        if k not in pts:
            print("警告：缺少 %s，生成的 config 可能不完整" % k, file=sys.stderr)

    ruleset_name = args.ruleset
    race_name = args.race
    class_name = args.klass
    gender_value = args.gender

    def need(k):
        if k not in pts:
            raise SystemExit("缺少必要采集点 %s，请重新 capture" % k)
        return pts[k]

    # click points for the configurable options
    rc = need("_ruleset")
    gm = need("_gender_male")
    gf = need("_gender_female")
    race = need("_race")
    klass = need("_class")

    # detection boxes generated around each clicked point (can be refined by `boxes`)
    cfg = {
        "meta": {
            "label": "%s / %s / %s / %s" % (ruleset_name, gender_value, race_name, class_name),
            "window_title": "魔兽世界",
            "screen": None,  # filled at end of capture
            "calibrated": True,
            "notes": "由交互式坐标采集生成；分辨率与显示器相关，换屏需重新采集。"
        },
        "names": {"main": args.main, "sub": args.sub},
        "ui": {k: pts[k] for k in pts if k.startswith("ui.")},
        "signals": {
            "green_box": _box_around(need("ui.name_sub"), 320, -20, 385, 30),
            "green_min": 15,
            "dialog_box": _box_around(need("ui.dialog_confirm"), -200, -25, 200, 25),
            "dialog_red_min": 0.45,
            "finish_box": _box_around(need("ui.finish"), -145, -18, 155, 18),
            "finish_red_max": 0.20,
        },
        "ruleset": {
            "value": ruleset_name,
            "options": {
                ruleset_name: {
                    "click": rc,
                    "detect": {"kind": "gold",
                               "box": _box_around(rc, -210, -430, -185, 430),
                               "min": 0.10}
                }
            }
        },
        "gender": {
            "value": gender_value,
            "options": {
                "男": {"click": gm, "detect": {"kind": "gender",
                                              "left_box": _box_around(gm, -65, -65, 65, 65),
                                              "right_box": _box_around(gf, -65, -65, 65, 65),
                                              "value": "male", "min": 0.20, "ratio": 1.8}},
                "女": {"click": gf, "detect": {"kind": "gender",
                                              "left_box": _box_around(gm, -65, -65, 65, 65),
                                              "right_box": _box_around(gf, -65, -65, 65, 65),
                                              "value": "female", "min": 0.20, "ratio": 1.8}},
            }
        },
        "race": {
            "value": race_name,
            "options": {
                race_name: {"click": race,
                            "detect": {"kind": "halo",
                                       "box": _box_around(race, -85, -85, 85, 85),
                                       "min_bright": 0.10, "min_gold": 0.02}}
            }
        },
        "class": {
            "value": class_name,
            "options": {
                class_name: {"click": klass,
                             "detect": {"kind": "bright",
                                        "box": _box_around(klass, -65, -70, 65, 70),
                                        "min": 0.12}}
            }
        },
        "timing": {
            "reconnect_wait": 6.0, "ruleset_wait": 6.0, "after_pick": 2.0,
            "to_naming": 3.0, "after_name": 1.5, "name_timeout": 10.0,
            "dialog_timeout": 12.0, "back_final_pause": 20.0,
        }
    }
    # screen size
    x = user32.GetSystemMetrics(76)
    y = user32.GetSystemMetrics(77)
    cfg["meta"]["screen"] = [user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)]
    cfg["meta"]["origin"] = [x, y]

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print("\n" + "=" * 60)
    print("已写入 %s" % args.out)
    print("屏幕 %dx%d (origin %d,%d)" % (cfg["meta"]["screen"][0],
          cfg["meta"]["screen"][1], x, y))
    print("请核对坐标表：")
    for k in sorted(pts):
        print("  %-24s %s" % (k, pts[k]))
    print("=" * 60)


def cmd_boxes(args):
    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    ruleset_name = cfg["ruleset"]["value"]
    race_name = cfg["race"]["value"]
    class_name = cfg["class"]["value"]
    targets = [
        ("ruleset.options.%s.detect.box" % ruleset_name, BOX_STEPS[0][1]),
        ("gender.options.男.detect.left_box", BOX_STEPS[1][1]),
        ("gender.options.女.detect.right_box", BOX_STEPS[2][1]),
        ("race.options.%s.detect.box" % race_name, BOX_STEPS[3][1]),
        ("class.options.%s.detect.box" % class_name, BOX_STEPS[4][1]),
        ("signals.green_box", BOX_STEPS[5][1]),
        ("signals.dialog_box", BOX_STEPS[6][1]),
        ("signals.finish_box", BOX_STEPS[7][1]),
    ]
    print("=" * 60)
    print("检测框框选：请先把游戏切到对应界面，按住左键拖拽框住区域。")
    print("=" * 60)
    for path, prompt in targets:
        print("\n>> %s" % prompt, flush=True)
        box = wait_drag()
        if box is None:
            print("超时，保留原值", flush=True)
            continue
        keys = path.split(".")
        node = cfg
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = box
        print("   更新 %s = %s" % (path, box), flush=True)
    with open(args.config, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print("\n已保存 %s" % args.config)


def cmd_probe(args):
    sys.path.insert(0, ".")
    import register
    cfg = register.load_config(args.config)
    register.do_probe(cfg)


def main():
    ap = argparse.ArgumentParser(description="交互式坐标采集（真实桌面）")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("capture", help="引导点击采集所有坐标并生成 config")
    p.add_argument("--out", default="draft.json")
    p.add_argument("--ruleset", default="PVP")
    p.add_argument("--race", required=True, help="目标种族名（部落/联盟）")
    p.add_argument("--klass", required=True, help="目标职业名")
    p.add_argument("--gender", default="男", choices=["男", "女"])
    p.add_argument("--main", required=True, help="主名")
    p.add_argument("--sub", required=True, help="副名")
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser("boxes", help="拖拽框选精确检测框")
    p.add_argument("--config", required=True)
    p.set_defaults(func=cmd_boxes)

    p = sub.add_parser("probe", help="验证检测框在当前屏幕的读数")
    p.add_argument("--config", required=True)
    p.set_defaults(func=cmd_probe)

    args = ap.parse_args()
    if not hasattr(args, "func"):
        ap.error("请指定子命令: capture / boxes / probe")
    args.func(args)


if __name__ == "__main__":
    main()
