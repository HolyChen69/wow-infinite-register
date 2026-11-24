# -*- coding: utf-8 -*-
"""Configuration-driven WoW character auto-registration on the REAL desktop.

All coordinates, detection boxes, thresholds, names and option choices come
from a JSON config file (see scripts/presets/). Nothing about a specific
ruleset / gender / race / class is hardcoded here.

Modes:
  trial     run a fixed number of rounds, save screenshots, print a summary
  infinite  run forever with auto-recovery from the client's 3D deadlock;
            stop only on real success (loop_success.png)

Utility:
  probe     print all current signals (for calibrating a new preset)
  validate  validate a config file without touching the desktop

Zero third-party dependencies; uses desktop_ctl (Win32 ctypes).
"""
import argparse
import itertools
import json
import os
import subprocess
import sys
import time

import desktop_ctl as d

DEFAULT_TIMING = {
    "after_focus": 1.0,
    "reconnect_wait": 6.0,
    "ruleset_wait": 6.0,
    "roster_wait": 4.0,
    "after_pick": 2.0,
    "pick_retry_pause": 1.0,
    "to_naming": 3.0,
    "after_name": 1.5,
    "name_timeout": 10.0,
    "dialog_timeout": 12.0,
    "after_dialog_confirm": 1.0,
    "back_pause": 1.0,
    "back_final_pause": 20.0,
    "cycle_pause": 1.0,
}

DIM_ORDER = ["gender", "race", "class"]
DIM_LABEL = {"ruleset": "规则集", "gender": "性别", "race": "种族", "class": "职业"}


# ----------------------------- config -----------------------------
def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    tm = dict(DEFAULT_TIMING)
    tm.update(cfg.get("timing", {}))
    cfg["timing"] = tm
    validate_config(cfg)
    return cfg


def validate_config(cfg):
    required_top = ["meta", "names", "ui", "signals", "ruleset", "gender", "race", "class"]
    missing = [k for k in required_top if k not in cfg]
    if missing:
        raise ValueError("config 缺少顶级字段: %s" % ", ".join(missing))
    for key in ["main", "sub"]:
        if not cfg["names"].get(key):
            raise ValueError("names.%s 不能为空" % key)
    for dim in ["ruleset", "gender", "race", "class"]:
        block = cfg[dim]
        value = block.get("value")
        opts = block.get("options", {})
        if value not in opts:
            raise ValueError("%s.value=%r 不在 options 中 (可选: %s)" %
                             (dim, value, ", ".join(opts) or "(空)"))
        opt = opts[value]
        if not opt.get("click"):
            raise ValueError("%s[%s] 缺少 click 坐标" % (dim, value))
        if not opt.get("detect"):
            raise ValueError("%s[%s] 缺少 detect 定义 — 未校准的选项不可使用" % (dim, value))
    meta = cfg.get("meta", {})
    if meta.get("recovery_mode") == "restart_client" and not (meta.get("client_exe") or meta.get("bnet_exe")):
        raise ValueError("recovery_mode=restart_client 需要 meta.client_exe 或 meta.bnet_exe")


def describe(cfg):
    parts = ["规则集=%s" % cfg["ruleset"]["value"],
             "性别=%s" % cfg["gender"]["value"],
             "种族=%s" % cfg["race"]["value"],
             "职业=%s" % cfg["class"]["value"],
             "主名=%s" % cfg["names"]["main"],
             "副名=%s" % cfg["names"]["sub"]]
    return " | ".join(parts)


# ----------------------------- sampling -----------------------------
def _rgb(px, w, x, y):
    o = (y * w + x) * 4
    return px[o + 2], px[o + 1], px[o]


def box_stats(box, step=3):
    """(gold_ratio, bright_ratio, mean_luminance, red_ratio) of a box [x0,y0,x1,y1]."""
    w, h, px = d.grab()
    x0, y0, x1, y1 = [int(v) for v in box]
    gold = bright = red = n = 0
    tot = 0.0
    for y in range(y0, y1, step):
        base = y * w * 4
        for x in range(x0, x1, step):
            o = base + x * 4
            r, g, b = px[o + 2], px[o + 1], px[o]
            lum = (r + g + b) / 3.0
            n += 1
            tot += lum
            if r > 170 and g > 130 and b < 160 and r > b * 1.25:
                gold += 1
            if lum > 110:
                bright += 1
            if r > 70 and g < 70 and b < 70 and r > g * 1.6 and r > b * 1.6:
                red += 1
    if n == 0:
        return 0.0, 0.0, 0.0, 0.0
    return gold / n, bright / n, tot / n, red / n


def halo_stats(box, step=2):
    """(bright_ratio, gold_ratio) inside a portrait halo box."""
    w, h, px = d.grab()
    x0, y0, x1, y1 = [int(v) for v in box]
    hi = gold = n = 0
    for y in range(y0, y1, step):
        base = y * w * 4
        for x in range(x0, x1, step):
            o = base + x * 4
            r, g, b = px[o + 2], px[o + 1], px[o]
            n += 1
            if (r + g + b) / 3.0 > 170:
                hi += 1
            if r > 200 and g > 170 and b < 150:
                gold += 1
    if n == 0:
        return 0.0, 0.0
    return hi / n, gold / n


def green_count(box, step=2):
    """Count green checkmark pixels in a box."""
    w, h, px = d.grab()
    x0, y0, x1, y1 = [int(v) for v in box]
    hits = 0
    for y in range(y0, y1, step):
        base = y * w * 4
        for x in range(x0, x1, step):
            o = base + x * 4
            r, g, b = px[o + 2], px[o + 1], px[o]
            if g > 90 and g > r * 1.3 and g > b * 1.25:
                hits += 1
    return hits


# ----------------------------- detection -----------------------------
def detect_option(opt):
    """Return (ok, info) for one option's `detect` block."""
    det = opt["detect"]
    kind = det["kind"]
    if kind == "gender":
        gl = box_stats(det["left_box"])[0]
        gr = box_stats(det["right_box"])[0]
        ratio = det.get("ratio", 1.8)
        if det.get("value") == "female":
            ok = gr > det["min"] and gr > gl * ratio
        else:
            ok = gl > det["min"] and gl > gr * ratio
        return ok, {"L": round(gl, 3), "R": round(gr, 3)}
    if kind == "gold":
        g = box_stats(det["box"])[0]
        return g > det["min"], {"gold": round(g, 3)}
    if kind == "bright":
        br = box_stats(det["box"])[1]
        return br > det["min"], {"bright": round(br, 3)}
    if kind == "halo":
        hb, hg = halo_stats(det["box"])
        ok = hb > det.get("min_bright", 0.10) and hg > det.get("min_gold", 0.02)
        return ok, {"bright": round(hb, 3), "gold": round(hg, 3)}
    raise ValueError("未知 detect.kind: %s" % kind)


def verify_dim(cfg, dim):
    sel = cfg[dim]["value"]
    ok, info = detect_option(cfg[dim]["options"][sel])
    return ok, info


def dialog_state(cfg):
    """((dialog_red, finish_red, green), is_imbalance_dialog)."""
    s = cfg["signals"]
    g = green_count(s["green_box"])
    dr = box_stats(s["dialog_box"], step=4)[3]
    fr = box_stats(s["finish_box"], step=3)[3]
    flag = (dr > s.get("dialog_red_min", 0.45)
            and fr < s.get("finish_red_max", 0.20)
            and g >= s.get("green_min", 15))
    return (round(dr, 3), round(fr, 3), g), flag


def login_state(cfg):
    """True when sitting on the clean login screen.

    finish_box frame-red on the login page is ~0.23 (the exit/menu buttons),
    ~0.78 on the naming page (red finish button) and ~0.0 on the dialog.
    A green check never appears on the login page. Use green + a mid-range red
    band so the login page is distinguished from both naming page and dialog.
    """
    s = cfg["signals"]
    g = green_count(s["green_box"])
    fr = box_stats(s["finish_box"], step=3)[3]
    lo = s.get("login_red_min", 0.15)
    hi = s.get("login_red_max", 0.65)
    return g < 10 and lo < fr < hi


def success_state(cfg):
    """True when the naming page is gone AND we are back on the roster list.

    Positive evidence required: green check gone, finish button not red, and the
    roster "create character" red button is present. A black/deadlocked screen or
    unknown popup has no roster button, so it must NOT be reported as success.
    """
    s = cfg["signals"]
    g = green_count(s["green_box"])
    fr = box_stats(s["finish_box"], step=3)[3]
    roster, _ = roster_present(cfg)
    return g < s.get("green_min", 15) and fr < 0.25 and roster


# ----------------------------- flow -----------------------------
def ensure_ruleset(cfg):
    sel = cfg["ruleset"]["value"]
    opt = cfg["ruleset"]["options"][sel]
    ok, info = detect_option(opt)
    for _ in range(3):
        if ok:
            return True, info
        d.click(*opt["click"])
        time.sleep(1.2)
        ok, info = detect_option(opt)
    return ok, info


def roster_present(cfg):
    """当前是否为角色列表屏（红色"创建角色"按钮在）。返回 (是否, 红比)。"""
    s = cfg["signals"]
    if "roster_box" not in s:
        return False, 0.0
    _, _, _, red = box_stats(s["roster_box"], step=4)
    return red > s.get("roster_red_min", 0.25), red


def _maybe_open_roster(cfg):
    """规则集与创建页之间偶尔出现角色列表屏；检测到红色"创建角色"才点击，否则直接进创建页。"""
    ui, tm, s = cfg["ui"], cfg["timing"], cfg["signals"]
    if "list_confirm" not in ui or "roster_box" not in s:
        return
    end = time.time() + tm.get("roster_wait", 4.0)
    while time.time() < end:
        _, _, _, red = box_stats(s["roster_box"], step=4)
        if red > s.get("roster_red_min", 0.30):
            d.click(*ui["list_confirm"])
            time.sleep(tm["ruleset_wait"])
            print("  角色列表屏: 点击创建角色", flush=True)
            return
        time.sleep(0.5)


def pick_all(cfg):
    """Click gender -> race -> class, then verify all three; retry up to 3x."""
    tm = cfg["timing"]
    info = {}
    for attempt in range(1, 4):
        for dim in DIM_ORDER:
            d.click(*cfg[dim]["options"][cfg[dim]["value"]]["click"])
            time.sleep(tm["after_pick"])
        all_ok = True
        for dim in DIM_ORDER:
            ok, inf = verify_dim(cfg, dim)
            info[dim] = inf
            if not ok:
                all_ok = False
        if all_ok:
            return True, info
        print("  pick 第%d次未通过: %s" % (attempt, info), flush=True)
        time.sleep(tm["pick_retry_pause"])
    return False, info


def fill_names(cfg):
    ui = cfg["ui"]
    for key, val in (("name_main", cfg["names"]["main"]),
                     ("name_sub", cfg["names"]["sub"])):
        d.click(*ui[key])
        time.sleep(0.6)
        d.key("ctrl+a")
        time.sleep(0.3)
        d.key("delete")
        time.sleep(0.4)
        d.type_text(val)
        time.sleep(1.0)


def wait_name_ok(cfg, seconds=None):
    """Return (ok, green_pixels, waited_seconds)."""
    seconds = seconds or cfg["timing"]["name_timeout"]
    s = cfg["signals"]
    start = time.time()
    end = start + seconds
    g = 0
    while True:
        g = green_count(s["green_box"])
        if g >= s.get("green_min", 15):
            return True, g, time.time() - start
        if time.time() >= end:
            return False, g, time.time() - start
        time.sleep(0.5)


def wait_dialog(cfg, seconds=None):
    seconds = seconds or cfg["timing"]["dialog_timeout"]
    end = time.time() + seconds
    while True:
        stats, flag = dialog_state(cfg)
        if flag:
            return stats, True
        if time.time() >= end:
            return stats, False
        time.sleep(0.4)


def build(cfg, start="login", submit_only=False):
    """One round. Returns (kind, dialog_stats, detail)."""
    t0 = time.time()
    tm, ui = cfg["timing"], cfg["ui"]
    if not submit_only:
        if start == "login":
            d.click(*ui["reconnect"])
            # 轮询等待列表屏或规则集卡片出现，避免页面仍在加载就盲检误判
            end_load = time.time() + tm["reconnect_wait"]
            while time.time() < end_load:
                if roster_present(cfg)[0]:
                    break
                if detect_option(cfg["ruleset"]["options"][cfg["ruleset"]["value"]])[0]:
                    break
                time.sleep(0.5)
        if start in ("login", "ruleset"):
            on_roster, rred = roster_present(cfg)
            if on_roster and "list_confirm" in ui:
                print("  列表屏直达: 点击创建角色 (red=%.3f)" % rred, flush=True)
                d.click(*ui["list_confirm"])
                time.sleep(tm["ruleset_wait"])
            else:
                ok, info = ensure_ruleset(cfg)
                print("  规则集 %s 检测=%s %s" % (cfg["ruleset"]["value"], ok, info), flush=True)
                if not ok:
                    return "safety_stop", (0, 0, 0), {
                        "why": "ruleset_not_selected", "nav_s": round(time.time() - t0, 2)}
                d.click(*ui["ruleset_confirm"])
                time.sleep(tm["ruleset_wait"])
                _maybe_open_roster(cfg)
        ok, info = pick_all(cfg)
        if not ok:
            return "drift_creation", (0, 0, 0), {
                "nav_s": round(time.time() - t0, 2), "char": info}
        d.click(*ui["custom_appearance"])
        time.sleep(tm["to_naming"])
        fill_names(cfg)
        time.sleep(tm["after_name"])

    ok, g, waited = wait_name_ok(cfg)
    if not ok:
        return "drift_named", (0, 0, 0), {
            "nav_s": round(time.time() - t0, 2), "namewait_s": round(waited, 2)}

    d.click(*ui["finish"])
    t1 = time.time()
    stats, flag = wait_dialog(cfg)
    return "submitted", stats, {
        "dialog_flag": bool(flag),
        "nav_s": round(time.time() - t0, 2),
        "wait_s": round(time.time() - t1, 2),
        "namewait_s": round(waited, 2)}


def go_back_to_login(cfg, from_creation=False):
    tm, ui = cfg["timing"], cfg["ui"]
    # 实测完整后退链: back_1 -> back_1 -> back_3（from_creation 仅作兼容保留）
    d.click(*ui["back_1"])
    time.sleep(tm["back_pause"])
    d.click(*ui["back_1"])
    time.sleep(tm["back_pause"])
    d.click(*ui["back_3"])
    time.sleep(tm["back_final_pause"])


def _find_create_button(px, w, h=None):
    """Locate the red 创建新角色 button by scanning the bottom-right quadrant.

    Uses screen-relative bounds so it survives a resolution change. Falls back
    to a fixed fraction of the current screen width/height.
    """
    sw, sh = d.metrics()
    x0, x1 = int(sw * 0.79), int(sw * 0.91)
    y0, y1 = int(sh * 0.86), int(sh * 0.93)
    pts = []
    for y in range(y0, y1, 3):
        for x in range(x0, x1, 3):
            r, g, b = _rgb(px, w, x, y)
            if r > 120 and g < 60 and b < 60:
                pts.append((x, y))
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2


def restore_to_login(cfg):
    """Recover from the client's 3D deadlock and return to clean login screen."""
    tm, ui = cfg["timing"], cfg["ui"]
    keyword = cfg["meta"].get("window_title", "魔兽世界")
    d.focus(keyword)
    time.sleep(1.0)
    if login_state(cfg):
        return True, "already_login"
    w, h, px = d.grab(force=True)
    center = _rgb(px, w, w // 2, h // 2)  # screen-relative 3D centre
    if max(center) < 45:  # 3D area deadlocked (pure black), UI frame still drawn
        btn = _find_create_button(px, w)
        if btn:
            d.click(*btn)
            time.sleep(7.0)
            print("  3D 死锁: 点击创建新角色 %s 重建渲染" % (btn,), flush=True)
    d.focus(keyword)
    time.sleep(0.5)
    # 实测完整后退链: back_1 -> back_1 -> back_3
    d.click(*ui["back_1"])
    time.sleep(1.5)
    d.click(*ui["back_1"])
    time.sleep(1.5)
    d.click(*ui["back_3"])
    time.sleep(5.0)
    return login_state(cfg), "recovered"


def restart_client(cfg):
    """NGA 实测路径：ALT+F4 关游戏（战网保留），确认退出后重开客户端。

    与客户端内后退重试相比，每轮大退一次可降低连续提交触发限流的概率。
    所有路径/进程名来自 config，便于在不同机器上校准。
    """
    meta, tm = cfg["meta"], cfg["timing"]
    proc = meta.get("client_proc", "Wow.exe")
    bnet = meta.get("bnet_proc", "Battle.net.exe")
    d.focus(meta.get("window_title", "魔兽世界"))
    time.sleep(0.8)
    d.alt_f4()
    # 等待游戏进程退出；超时则强制结束
    end = time.time() + tm.get("restart_close_wait", 8.0)
    while time.time() < end and d.process_running(proc):
        time.sleep(0.5)
    if d.process_running(proc):
        d.kill_process(proc)
        time.sleep(1.0)
    # NGA 实测路径：ALT+F4 关游戏后保留战网，回战网点"进入游戏"重新拉起客户端。
    if meta.get("bnet_exe") and not d.process_running(bnet):
        d.start_process(meta["bnet_exe"])
        time.sleep(tm.get("restart_boot_wait", 25.0))
    d.focus("战网")
    time.sleep(1.0)
    enter = meta.get("bnet_enter")
    if enter:
        d.click(*enter)
    elif meta.get("client_exe"):
        d.start_process(meta["client_exe"])
    time.sleep(tm.get("restart_boot_wait", 25.0))
    print("  客户端已通过战网'进入游戏'重启，等待登录界面", flush=True)


# ----------------------------- run loops -----------------------------
def run(cfg, mode, rounds, tag, infinite=False):
    """Shared state machine. mode only controls round count + screenshot policy."""
    state = "login"
    log_path = "register_%s.jsonl" % tag
    t_all = time.time()
    iterator = itertools.count(1) if infinite else range(1, rounds + 1)
    total_success = False
    open(log_path, "w", encoding="utf-8").close()

    for i in iterator:
        rtag = "%s_r%03d" % (tag, i)
        print("=== 第%d轮 [%s] state=%s ===" % (i, mode, state), flush=True)
        t_round = time.time()
        d.focus(cfg["meta"].get("window_title", "魔兽世界"))
        time.sleep(cfg["timing"]["after_focus"])

        if state == "safety_stop":
            ok, how = restore_to_login(cfg)
            print("  安全停机恢复: %s / %s" % (ok, how), flush=True)
            state = "login"
            time.sleep(2.0)
        if state.startswith("drift"):
            _, up = dialog_state(cfg)
            if up:
                d.click(*cfg["ui"]["dialog_confirm"])
                time.sleep(cfg["timing"]["after_dialog_confirm"])
            go_back_to_login(cfg, from_creation=(state == "drift_creation"))
        elif state == "dialog":
            d.click(*cfg["ui"]["dialog_confirm"])
            time.sleep(cfg["timing"]["after_dialog_confirm"])
            if cfg["meta"].get("recovery_mode", "in_client") == "restart_client":
                restart_client(cfg)
            else:
                go_back_to_login(cfg)
                d.focus(cfg["meta"].get("window_title", "魔兽世界"))
                time.sleep(0.5)

        kind, stats, detail = build(cfg, start="login")
        total = round(time.time() - t_round, 2)
        dialog_flag = bool(detail.get("dialog_flag", False))

        rec = {"round": i, "kind": kind, "dialog": dialog_flag,
               "dialog_red": stats[0], "finish_red": stats[1], "green": stats[2],
               "total_s": total, "detail": detail}
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print("R%d %s 信号=(%.3f,%.3f,%d) 耗时=%.1fs" %
              (i, kind, stats[0], stats[1], stats[2], total), flush=True)

        if not infinite:
            d.shot("register_%s.png" % rtag)

        if kind == "safety_stop":
            state = "safety_stop"
            if not infinite:
                print("!! 规则集未选中, 试跑中止 (不提交)", flush=True)
                break
            continue
        if kind.startswith("drift"):
            state = kind
            continue
        if dialog_flag:
            state = "dialog"
            continue
        if success_state(cfg):
            print(">>> 疑似成功: 命名页已消失 (第%d轮), 复核中..." % i, flush=True)
            time.sleep(5.0)
            still = success_state(cfg) and not login_state(cfg)
            if still:
                d.shot("loop_success.png")
                print(">>> 复核通过: 确认建号成功", flush=True)
                total_success = True
                break
            print("  复核未通过, 继续重试", flush=True)
            state = "dialog"
            continue
        # 非弹窗、非成功：未知/超时屏（限流弹窗暂未建模，属此类）。
        # 加长退避，避免硬刷延长风控冷却；待现场采集限流信号后再建专用分支。
        backoff = cfg["timing"].get("unknown_backoff", 180.0)
        print("  未知/超时屏, 退避 %.0fs 后再试" % backoff, flush=True)
        time.sleep(backoff)
        state = "dialog"

    print("=== 结束: 共%d轮, 耗时%.1fs, 成功=%s ===" %
          (i, time.time() - t_all, total_success), flush=True)
    return total_success


def do_probe(cfg):
    print("配置: %s" % describe(cfg))
    s = cfg["signals"]
    print("绿勾=%d  弹窗红=%.3f  完成红=%.3f" %
          (green_count(s["green_box"]),
           box_stats(s["dialog_box"], step=4)[3],
           box_stats(s["finish_box"], step=3)[3]))
    print("弹窗判定=%s  登录页=%s  成功态=%s" %
          (dialog_state(cfg)[1], login_state(cfg), success_state(cfg)))
    for dim in DIM_ORDER + ["ruleset"]:
        ok, info = verify_dim(cfg, dim)
        print("%s %s: %s %s" % (DIM_LABEL[dim], cfg[dim]["value"], ok, info))


def focus_agent():
    """把前台焦点切回 AI Agent(WorkBuddy) 窗口。

    游戏是全屏的，脚本停机后若不切回，用户看着游戏画面根本察觉不到已经停了。
    调用点在 main() 的 finally 里，覆盖试跑结束/安全停机/异常崩溃所有退出路径。
    """
    try:
        now = None
        for kw in ["豆包工作", "操控学习电脑操作", "WorkBuddy"]:
            d.focus(kw)
            time.sleep(0.4)
            now = d.window_title(d.user32.GetForegroundWindow())
            if kw.lower() in (now or "").lower():
                break
        print(">>> 已切回 AI Agent 窗口: %r" % now, flush=True)
    except Exception as e:
        print(">>> 切回 AI Agent 失败: %s" % e, flush=True)


def main():
    ap = argparse.ArgumentParser(description="配置驱动的魔兽世界角色自动注册")
    ap.add_argument("--config", help="config JSON 路径")
    ap.add_argument("--mode", choices=["trial", "infinite", "probe", "validate"],
                    default="validate")
    ap.add_argument("--rounds", type=int, default=3, help="trial 模式的轮数 (默认3)")
    ap.add_argument("--tag", default="reg", help="日志/截图标签")
    args = ap.parse_args()

    if args.mode == "validate":
        if not args.config:
            ap.error("validate 需要 --config")
        cfg = load_config(args.config)
        print("配置校验通过: %s" % describe(cfg))
        return

    if not args.config:
        ap.error("需要 --config")
    cfg = load_config(args.config)

    if args.mode == "probe":
        do_probe(cfg)
        return

    infinite = (args.mode == "infinite")
    try:
        if infinite:
            base = os.path.getmtime("loop_success.png") if os.path.exists("loop_success.png") else 0
            print(">>> 无限模式启动: %s" % describe(cfg), flush=True)
            success = run(cfg, args.mode, 0, args.tag, infinite=True)
            now = os.path.getmtime("loop_success.png") if os.path.exists("loop_success.png") else 0
            print(">>> 无限模式结束 success=%s flag_updated=%s" %
                  (success, now > base), flush=True)
            if success and cfg.get("meta", {}).get("shutdown_on_success", False):
                delay = int(cfg.get("meta", {}).get("shutdown_delay_sec", 60))
                print(">>> 建号成功, %d 秒后关机; 如需取消请运行 shutdown /a" % delay, flush=True)
                shutdown_exe = r"C:\Windows\System32\shutdown.exe"
                try:
                    subprocess.run([shutdown_exe, "/s", "/t", str(delay),
                                    "/c", "WoW 角色创建成功，即将关机"], check=False)
                except Exception as e:
                    print(">>> 关机命令执行失败(不影响成功留痕): %s" % e, flush=True)
        else:
            print(">>> 试跑 %d 轮: %s" % (args.rounds, describe(cfg)), flush=True)
            success = run(cfg, args.mode, args.rounds, args.tag)
            print(">>> 试跑结束 success=%s" % success, flush=True)
    finally:
        # 无论正常跑完、安全停机中止、还是异常崩溃，都把焦点交还 AI Agent，
        # 避免游戏全屏遮住导致用户察觉不到脚本已停止。
        focus_agent()


if __name__ == "__main__":
    main()
