"""Zero-dependency real-desktop control via Win32 API (ctypes).

Commands:
  shot <path.bmp>            capture whole virtual screen
  info                       screen size + foreground window + top-level windows
  click <x> <y>              left click
  dblclick <x> <y>           double left click
  move <x> <y>               move cursor
  type <text>                unicode text input (works for Chinese)
  key <name>                 enter/tab/esc/backspace/ctrl+v/...
  sleep <seconds>
  batch <file>               run lines of commands from a file
"""
import ctypes
import os
import struct
import subprocess
import sys
import time
import zlib
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.SetActiveWindow.argtypes = [wintypes.HWND]
user32.SetActiveWindow.restype = wintypes.HWND
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_void_p]

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

# ---------- structures ----------
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p)]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]

class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", INPUTUNION)]

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0002, 0x0004

# ---------- screen ----------
def metrics():
    x = user32.GetSystemMetrics(76)
    y = user32.GetSystemMetrics(77)
    w = user32.GetSystemMetrics(78)
    h = user32.GetSystemMetrics(79)
    return x, y, w, h

def read_png(path):
    """Decode the simple filter-0 RGB PNGs produced by write_png()."""
    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG produced by this tool")
    pos, idat, w, h = 8, b"", 0, 0
    while pos < len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if tag == b"IHDR":
            w, h = struct.unpack(">II", chunk[:8])
        elif tag == b"IDAT":
            idat += chunk
        elif tag == b"IEND":
            break
    raw = zlib.decompress(idat)
    stride = w * 3
    rows = []
    for y in range(h):
        off = y * (stride + 1)
        rows.append(raw[off + 1:off + 1 + stride])
    return w, h, rows

def cropfile(src, dst, x, y, cw, ch):
    w, h, rows = read_png(src)
    x, y, cw, ch = [int(v) for v in (x, y, cw, ch)]
    cw, ch = min(cw, w - x), min(ch, h - y)
    out = [rows[yy][x * 3:(x + cw) * 3] for yy in range(y, y + ch)]
    raw = b"".join(b"\x00" + r for r in out)
    ihdr = struct.pack(">IIBBBBB", cw, ch, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr)
           + _png_chunk(b"IDAT", zlib.compress(raw, 6))
           + _png_chunk(b"IEND", b""))
    with open(dst, "wb") as f:
        f.write(png)
    print("CROPFILE %s -> %s (%d,%d %dx%d)" % (src, dst, x, y, cw, ch))

def _png_chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

def write_png(path, w, h, bgra):
    """bgra: bytes, top-down, 4 bytes per pixel -> RGB PNG"""
    stride = w * 4
    raw = bytearray()
    for y in range(h):
        row = bgra[y * stride:(y + 1) * stride]
        rgb = bytearray(w * 3)
        rgb[0::3] = row[2::4]
        rgb[1::3] = row[1::4]
        rgb[2::3] = row[0::4]
        raw.append(0)  # filter: None
        raw.extend(rgb)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-bit RGB
    data = (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + _png_chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(data)

def shot(path, region=None):
    if region:
        sx, sy, w, h = [int(v) for v in region]
    else:
        sx, sy, w, h = metrics()
    hdc = user32.GetDC(None)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(memdc, bmp)
    gdi32.BitBlt(memdc, 0, 0, w, h, hdc, sx, sy, 0x00CC0020)  # SRCCOPY
    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth, bi.biHeight = w, -h  # top-down
    bi.biPlanes, bi.biBitCount, bi.biCompression = 1, 32, 0
    size = w * h * 4
    buf = (ctypes.c_ubyte * size)()
    gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    pixels = bytes(buf)
    if path.lower().endswith(".png"):
        write_png(path, w, h, pixels)
    else:
        header = struct.pack("<2sIHHI", b"BM", 14 + 40 + size, 0, 0, 54)
        infohdr = struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 32, 0, size, 0, 0, 0, 0)
        with open(path, "wb") as f:
            f.write(header)
            f.write(infohdr)
            f.write(pixels)
    gdi32.SelectObject(memdc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(None, hdc)
    print("SHOT %s %dx%d origin=(%d,%d)" % (path, w, h, sx, sy))

def window_title(hwnd):
    b = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, b, 512)
    return b.value

def info():
    x, y, w, h = metrics()
    print("VIRTUAL_SCREEN origin=(%d,%d) size=%dx%d" % (x, y, w, h))
    fg = user32.GetForegroundWindow()
    print("FOREGROUND hwnd=%s title=%r" % (fg, window_title(fg)))
    titles = []
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        if user32.IsWindowVisible(hwnd):
            t = window_title(hwnd)
            if t.strip():
                r = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                titles.append((t, (r.left, r.top, r.right, r.bottom)))
        return True
    user32.EnumWindows(EnumProc(cb), 0)
    for t, r in titles:
        print("WINDOW %r rect=%s" % (t, r))

def focus(keyword):
    """Bring a top-level window whose title contains keyword to the foreground."""
    found = []
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        t = window_title(hwnd)
        if keyword.lower() in t.lower() and user32.IsWindowVisible(hwnd):
            found.append((hwnd, t))
        return True
    user32.EnumWindows(EnumProc(cb), 0)
    if not found:
        print("FOCUS FAILED: no window matching %r" % keyword)
        return
    hwnd, title = found[0]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE only when minimized
    # Windows foreground-lock: a background process cannot SetForegroundWindow
    # directly. Two standard tricks make it succeed reliably:
    #   1) AttachThreadInput to the current foreground thread;
    #   2) send a harmless ALT key press (simulated user input).
    fg = user32.GetForegroundWindow()
    cur_tid = kernel32.GetCurrentThreadId()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(fg_tid and fg_tid != cur_tid and
                    user32.AttachThreadInput(cur_tid, fg_tid, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.keybd_event(0x12, 0, 0, None)  # ALT down
        user32.keybd_event(0x12, 0, 2, None)  # ALT up (KEYEVENTF_KEYUP)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(cur_tid, fg_tid, False)
    time.sleep(0.6)
    now = window_title(user32.GetForegroundWindow())
    ok = (user32.GetForegroundWindow() == hwnd)
    print("FOCUS %r hwnd=%s foreground_now=%r ok=%s" % (title, hwnd, now, ok))
    return ok

_GRAB = {"t": 0.0, "w": 0, "h": 0, "px": None}


def grab(force=False, ttl=0.08):
    """Grab the whole virtual screen into memory once.

    Returns (width, height, bgra_bytes). GDI GetPixel() costs ~16ms per call
    while a DirectX game owns the display, so 2867 sampled points used to take
    ~48 seconds. One BitBlt + GetDIBits costs ~0.1s. A short-lived cache lets
    screen_signals() reuse a single grab for all three sample regions.
    """
    now = time.time()
    c = _GRAB
    if not force and c["px"] is not None and now - c["t"] < ttl:
        return c["w"], c["h"], c["px"]
    x, y, w, h = metrics()
    hdc = user32.GetDC(None)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(memdc, bmp)
    gdi32.BitBlt(memdc, 0, 0, w, h, hdc, x, y, 0x00CC0020)  # SRCCOPY
    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth, bi.biHeight = w, -h  # top-down
    bi.biPlanes, bi.biBitCount, bi.biCompression = 1, 32, 0
    buf = (ctypes.c_ubyte * (w * h * 4))()
    gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    px = bytes(buf)
    gdi32.SelectObject(memdc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(None, hdc)
    c.update(t=now, w=w, h=h, px=px)
    return w, h, px


def region_stats(x, y, w, h, step=4):
    """Return (red_ratio, dark_ratio) sampled from a screen rectangle."""
    sw, sh, px = grab()
    n = red = dark = 0
    for yy in range(y, y + h, step):
        base = yy * sw * 4
        for xx in range(x, x + w, step):
            o = base + xx * 4
            r, g, b = px[o + 2], px[o + 1], px[o]   # stored BGRA
            n += 1
            if r > 70 and g < 70 and b < 70 and r > g * 1.6 and r > b * 1.6:
                red += 1
            if r < 70 and g < 70 and b < 70:
                dark += 1
    return red / max(n, 1), dark / max(n, 1)

def green_check_count():
    """Count green pixels in the name-valid checkmark area."""
    sw, sh, px = grab()
    hits = 0
    for y in range(80, 126, 2):
        for x in range(2565, 2622, 2):
            o = (y * sw + x) * 4
            r, g, b = px[o + 2], px[o + 1], px[o]
            if g > 90 and g > r * 1.3 and g > b * 1.25:
                hits += 1
    return hits

def screen_signals():
    """(green_checkmark_pixels, dialog_button_red_ratio, finish_button_red_ratio)"""
    g = green_check_count()
    dred, _ = region_stats(1720, 1120, 400, 45, 4)
    fred, _ = region_stats(3400, 2030, 300, 30, 3)
    return g, dred, fred

def is_dialog():
    """True only for the real 'faction population imbalance' dialog.

    Requires all three: red 确定 button, greyed-out 完成, and a valid name
    (green check) still present. This rejects drifted states where a red/gold
    hairstyle fooled a pure-colour detector.
    """
    g, dred, fred = screen_signals()
    return (dred, fred, g), (dred > 0.45 and fred < 0.20 and g >= 15)

def name_ok():
    return green_check_count() >= 15


# ---------- character-creation verification ----------
# Regions measured on the creation screen at 3840x2160. A gold selection frame
# is the only reliable signal: portrait artwork / model lighting drift between
# rounds, but the highlight border stays at a fixed position.
CHK_BOXES = {
    "gender_L": (1785, 20, 1915, 150),     # male icon
    "gender_R": (1925, 20, 2055, 150),     # female icon
    "druid":    (2370, 1930, 2500, 2070),  # druid class icon
}

# Halo around the 5th horde portrait (塑风者天裔 / Skydancer). The gold
# SELECTION frame lives in this ring; sampling it (instead of the portrait
# artwork) makes the check immune to the portrait changing with gender.
HALO_BOX = (410, 895, 585, 1070)


def selection_halo():
    """(bright_ratio, gold_ratio) of the 4th horde portrait's selection halo."""
    sw, sh, px = grab()
    x0, y0, x1, y1 = HALO_BOX
    hi = gold = n = 0
    for y in range(y0, y1, 2):
        base = y * sw * 4
        for x in range(x0, x1, 2):
            o = base + x * 4
            r, g, b = px[o + 2], px[o + 1], px[o]
            n += 1
            if (r + g + b) / 3.0 > 170:
                hi += 1
            if r > 200 and g > 170 and b < 150:
                gold += 1
    return hi / max(n, 1), gold / max(n, 1)


def box_features(x0, y0, x1, y1, step=3):
    """(gold_ratio, bright_ratio, mean_luminance) of a screen rectangle."""
    sw, sh, px = grab()
    gold = bright = n = 0
    tot = 0.0
    for y in range(y0, y1, step):
        base = y * sw * 4
        for x in range(x0, x1, step):
            o = base + x * 4
            r, g, b = px[o + 2], px[o + 1], px[o]   # stored BGRA
            lum = (r + g + b) / 3.0
            n += 1
            tot += lum
            if r > 170 and g > 130 and b < 160 and r > b * 1.25:
                gold += 1
            if lum > 110:
                bright += 1
    return gold / max(n, 1), bright / max(n, 1), tot / max(n, 1)


def character_ok(force=True, verbose=False):
    """True only when the creation screen shows MALE + SKYDANCER + DRUID.

    Calibrated against real screenshots:
      male      : gender_L gold 0.258~0.277 / gender_R 0.074~0.078
      female    : gender_L gold 0.100 / gender_R 0.215
      skydancer : halo bright 0.270 / gold 0.070 (selected)
      (unsel)   : halo bright 0.000 / gold 0.000
      druid     : bright 0.185~0.299 (selected) vs 0.000~0.076 (not)
    """
    if force:
        grab(force=True)          # clicks just happened, do not reuse a stale frame
    f = {k: box_features(*v) for k, v in CHK_BOXES.items()}
    gl, gr = f["gender_L"][0], f["gender_R"][0]
    hb, hg = selection_halo()
    db = f["druid"][1]
    male = gl > 0.20 and gl > gr * 1.8
    skydancer = hb > 0.10 and hg > 0.02
    druid = db > 0.12
    info = {"male": male, "skydancer": skydancer, "druid": druid,
            "gender_gold": (round(gl, 3), round(gr, 3)),
            "halo_bright_gold": (round(hb, 3), round(hg, 3)),
            "druid_bright": round(db, 3)}
    if verbose:
        print("CHAR male=%s(%.3f/%.3f) skydancer=%s(%.3f/%.3f) "
              "druid=%s(%.3f) -> %s" %
              (male, gl, gr, skydancer, hb, hg, druid, db,
               male and skydancer and druid), flush=True)
    return male and skydancer and druid, info


# ---------- ruleset verification ----------
# Left gold border of the 2nd (PvP) card on the ruleset screen.
PVP_FRAME_BOX = (1460, 700, 1485, 1560)
PVP_CARD_CLICK = (1665, 1130)


def pvp_selected():
    """(gold_ratio, selected) for the PvP ruleset card.

    Calibrated: PvP highlighted -> 0.221, another card highlighted -> 0.000.
    The client can remember a previously chosen ruleset, so this must be
    checked on every round instead of assuming PvP is the default.
    """
    grab(force=True)
    x0, y0, x1, y1 = PVP_FRAME_BOX
    sw, sh, px = grab()
    hit = n = 0
    for y in range(y0, y1, 2):
        base = y * sw * 4
        for x in range(x0, x1, 2):
            o = base + x * 4
            r, g, b = px[o + 2], px[o + 1], px[o]
            n += 1
            if r > 190 and g > 150 and b < 130 and r > b * 1.8:
                hit += 1
    ratio = hit / max(n, 1)
    return round(ratio, 3), ratio > 0.10


# ---------- process management ----------
TH32CS_SNAPPROCESS = 0x00000002
PROCESS_TERMINATE = 0x0001


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(wintypes.ULONG)),
        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
    ]


def pids_by_name(exe):
    """Return PIDs whose executable name matches (case-insensitive)."""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap:
        return []
    pe = PROCESSENTRY32W()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    out = []
    try:
        if kernel32.Process32FirstW(snap, ctypes.byref(pe)):
            while True:
                if pe.szExeFile.lower() == exe.lower():
                    out.append(pe.th32ProcessID)
                if not kernel32.Process32NextW(snap, ctypes.byref(pe)):
                    break
    finally:
        kernel32.CloseHandle(snap)
    return out


def process_running(exe):
    return len(pids_by_name(exe)) > 0


def kill_process(exe):
    """Terminate all processes matching exe name."""
    for pid in pids_by_name(exe):
        h = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if h:
            kernel32.TerminateProcess(h, 0)
            kernel32.CloseHandle(h)
            time.sleep(0.3)


def start_process(path):
    """Launch an executable detached from the script console."""
    subprocess.Popen([path], cwd=os.path.dirname(path), close_fds=True)


def alt_f4():
    """Send ALT+F4 to the foreground window (close game client)."""
    user32.keybd_event(0x12, 0, 0, None)          # ALT down
    time.sleep(0.1)
    user32.keybd_event(0x73, 0, 0, None)         # F4 down
    time.sleep(0.1)
    user32.keybd_event(0x73, 0, KEYEVENTF_KEYUP, None)
    user32.keybd_event(0x12, 0, KEYEVENTF_KEYUP, None)
    print("KEY ALT+F4")


# ---------- mouse ----------
def move(x, y):
    user32.SetCursorPos(int(x), int(y))

def _mouse_event(flags):
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.u.mi.dwFlags = flags
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

def click(x, y, count=1):
    move(x, y)
    time.sleep(0.20)
    for _ in range(count):
        _mouse_event(MOUSEEVENTF_LEFTDOWN)
        time.sleep(0.07)
        _mouse_event(MOUSEEVENTF_LEFTUP)
        time.sleep(0.12)
    print("CLICK (%s,%s) x%d" % (x, y, count))

# ---------- keyboard ----------
VK = {
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B,
    "backspace": 0x08, "delete": 0x2E, "space": 0x20,
    "ctrl": 0x11, "shift": 0x10, "alt": 0x12,
    "a": 0x41, "v": 0x56, "c": 0x43, "x": 0x58,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
}

def _send_key(vk, scan, flags, unicode_mode=False):
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    if unicode_mode:
        inp.u.ki.wVk = 0
        inp.u.ki.wScan = scan
        inp.u.ki.dwFlags = flags | KEYEVENTF_UNICODE
    else:
        inp.u.ki.wVk = vk
        inp.u.ki.wScan = scan
        inp.u.ki.dwFlags = flags
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

def type_text(text):
    for ch in text:
        code = ord(ch)
        # BMP characters (incl. CJK) via VK_PACKET
        _send_key(0, code, 0, True)
        time.sleep(0.02)
        _send_key(0, code, KEYEVENTF_KEYUP, True)
        time.sleep(0.06)
    print("TYPE %r" % text)

def key(name):
    name = name.lower()
    if name.startswith("ctrl+"):
        sub = name.split("+", 1)[1]
        _send_key(VK["ctrl"], 0, 0)
        time.sleep(0.05)
        _send_key(VK.get(sub, 0), 0, 0)
        time.sleep(0.05)
        _send_key(VK.get(sub, 0), 0, KEYEVENTF_KEYUP)
        _send_key(VK["ctrl"], 0, KEYEVENTF_KEYUP)
    else:
        v = VK.get(name, 0)
        _send_key(v, 0, 0)
        time.sleep(0.06)
        _send_key(v, 0, KEYEVENTF_KEYUP)
    print("KEY %s" % name)

def set_clipboard(text):
    # CF_UNICODETEXT = 13
    user32.OpenClipboard(None)
    user32.EmptyClipboard()
    data = ctypes.create_unicode_buffer(text)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    h = kernel32.GlobalAlloc(0x2000, (len(text) + 1) * 2)
    p = kernel32.GlobalLock(h)
    ctypes.memmove(p, data, (len(text) + 1) * 2)
    kernel32.GlobalUnlock(h)
    user32.SetClipboardData(13, h)
    user32.CloseClipboard()
    print("CLIPBOARD %r" % text)

# ---------- dispatch ----------
def run_line(line):
    line = line.strip()
    if not line or line.startswith("#"):
        return
    parts = line.split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""
    if cmd == "shot":
        bits = arg.split()
        region = [int(v) for v in bits[1:5]] if len(bits) >= 5 else None
        shot(bits[0], region)
    elif cmd == "info":
        info()
    elif cmd == "focus":
        focus(arg)
    elif cmd == "cropfile":
        a = arg.split()
        cropfile(a[0], a[1], *a[2:6])
    elif cmd == "probe":
        g, dred, fred = screen_signals()
        _, flag = is_dialog()
        print("PROBE green=%d dialog_red=%.3f finish_red=%.3f dialog=%s name_ok=%s"
              % (g, dred, fred, flag, name_ok()))
    elif cmd == "char":
        ok, detail = character_ok(verbose=True)
        print("CHAR_OK %s %s" % (ok, detail))
    elif cmd == "pvp":
        ratio, ok = pvp_selected()
        print("PVP_SELECTED gold=%.3f ok=%s" % (ratio, ok))
    elif cmd == "click":
        a, b = arg.split()[:2]
        click(int(a), int(b))
    elif cmd == "dblclick":
        a, b = arg.split()[:2]
        click(int(a), int(b), 2)
    elif cmd == "move":
        a, b = arg.split()[:2]
        move(int(a), int(b))
        print("MOVE (%s,%s)" % (a, b))
    elif cmd == "type":
        type_text(arg)
    elif cmd == "key":
        key(arg.strip())
    elif cmd == "clip":
        set_clipboard(arg)
    elif cmd == "sleep":
        time.sleep(float(arg))
        print("SLEEP %s" % arg)
    elif cmd == "echo":
        print("ECHO %s" % arg)
    elif cmd == "batch":
        with open(arg, "r", encoding="utf-8") as f:
            for l in f:
                run_line(l)
    else:
        raise ValueError("unknown command: " + cmd)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "batch":
        run_line("batch " + sys.argv[2])
    else:
        run_line(" ".join(sys.argv[1:]))
