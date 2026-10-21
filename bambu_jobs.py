"""นับกรัมเส้นต่องานของเครื่อง Bambu (ต่อตรงในวง LAN เท่านั้น)

ตอนงานเริ่ม: ดึงไฟล์งาน (.3mf) จากที่เก็บไฟล์ของเครื่อง (FTPS พอร์ต 990 ด้วย access code) อ่าน Metadata/slice_info.config
  ได้กรัมที่สไลซ์ไว้ของแต่ละเส้นในเพลตที่ปริ้น (วิธีเดียวกับ FilaBridge) อ่านในเครื่องนี้ ไม่ส่งไฟล์ไปไหน
  และไล่คำสั่งปริ้น (Metadata/plate_N.gcode) บวกเส้นที่ฉีดไปจริงของแต่ละเส้นสะสมทีละชั้น
ตอนงานจบ: เสร็จ = ใช้ครบตามไฟล์, ล้มเหลว / หยุดกลางทาง = กรัมทั้งงาน x สัดส่วนเส้นที่ฉีดไปถึงชั้นที่หยุด (จาก gcode) = ส่วนที่เสียไป
  ไม่มี gcode = ใช้สัดส่วนชั้น (ไม่มีชั้นใช้ %) แทน หยาบกว่า เพราะชั้นล่างที่มีฐานกับ support ใช้เส้นมากกว่า
  ส่งเป็นรายการงานเข้า teewang พร้อมสถานะเครื่อง (POST /api/feed ช่อง jobs)

ponytail: ยังไม่ได้ลองกับเครื่องจริง ที่อยู่ไฟล์บนเครื่องเดาตามที่คนอื่นแกะไว้ (/cache, ราก, /model) หาไม่เจอ = งานนั้นไม่รู้กรัม
  กรัมทั้งงานเป็นเลขที่โปรแกรมสไลซ์คำนวณ (ตัวเดียวกับที่ Bambu Studio โชว์) ไม่ได้ชั่งจริง คลาดตามขนาดเส้นจริงกับความหนาแน่นที่ตั้งไว้
  agent รีสตาร์ทกลางงาน = งานเดียวกันได้สองรายการ (นับเวลาเริ่มใหม่) ยอมไว้ก่อน
"""

import ftplib
import hashlib
import re
import ssl
import tempfile
import threading
import xml.etree.ElementTree as ET
import zipfile

ACTIVE = ("PREPARE", "SLICING", "RUNNING", "PAUSE")
MAX_FILE = 300 * 1024 * 1024  # ไฟล์งานใหญ่กว่านี้ไม่ดึง


class ImplicitFTPS(ftplib.FTP_TLS):
    """FTPS แบบ implicit (TLS ตั้งแต่ต่อ) ที่เครื่อง Bambu ใช้ ช่องส่งไฟล์ต้องใช้ TLS session เดียวกับช่องคำสั่ง ไม่งั้น LIST/RETR ค้าง"""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._sock = None

    @property
    def sock(self):
        return self._sock

    @sock.setter
    def sock(self, value):
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value)
        self._sock = value

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, session=self.sock.session)
        return conn, size


def ftps(x, timeout=20):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # เครื่องใช้ใบรับรองที่เซ็นเอง
    f = ImplicitFTPS(context=ctx)
    f.connect(x["host"], 990, timeout=timeout)
    f.login("bblp", x["access_code"])
    f.prot_p()
    return f


def plate_of(gcode_file):
    """เลขเพลตจากชื่อ gcode ในรายงาน เช่น /data/Metadata/plate_2.gcode = 2 ไม่รู้ = None"""
    m = re.search(r"plate_(\d+)\.gcode", str(gcode_file or ""))
    return int(m.group(1)) if m else None


def candidates(name, gcode_file):
    """ที่อยู่ไฟล์งานที่น่าจะเป็น เรียงจากน่าจะใช่ที่สุด"""
    out = []
    g = str(gcode_file or "")
    if g.lower().endswith(".3mf"):
        out.append("/" + re.sub(r"^/?(sdcard|mnt/sdcard)/", "", g).lstrip("/"))
    if name:
        # Bambu Studio ส่งไฟล์ที่สไลซ์แล้วชื่อ <งาน>.gcode.3mf บางทีก็ <งาน>.3mf
        names = [name] if name.lower().endswith(".3mf") else [f"{name}.gcode.3mf", f"{name}.3mf"]
        out += [f"{d}/{n}" for d in ("/cache", "", "/model") for n in names]
    return list(dict.fromkeys(out))


def clean_type(t):
    """ชนิดเส้นเหลือแค่ตัวที่เว็บรับ (lib/jobs.ts, lib/feeds.ts) ว่าง = ?"""
    return re.sub(r"[^A-Za-z0-9+\-_ .]", "", str(t or "")).strip()[:16] or "?"


def parse_slice(xml_text, plate=None):
    """กรัมของแต่ละเส้นในเพลตจาก slice_info.config คืน [{type, color, grams}] หรือ None ถ้าไม่มีเพลตที่ใช้ได้"""
    root = ET.fromstring(xml_text)
    plates = root.findall("plate")
    idx = lambda p: next((m.get("value") for m in p.findall("metadata") if m.get("key") == "index"), None)
    pick = [p for p in plates if plate is not None and idx(p) == str(plate)] or [p for p in plates if p.findall("filament")]
    if not pick:
        return None
    out = []
    for f in pick[0].findall("filament"):
        try:
            g = round(float(f.get("used_g") or 0), 2)
        except ValueError:
            continue
        if g <= 0:
            continue
        color = (f.get("color") or "").lstrip("#").upper()[:6]
        try:
            tool = int(f.get("id")) - 1  # filament id ในไฟล์นับจาก 1 คำสั่ง T ใน gcode นับจาก 0
        except (TypeError, ValueError):
            tool = None
        out.append({"type": clean_type(f.get("type")), "color": color if re.fullmatch(r"[0-9A-F]{6}", color) else None, "grams": g, "tool": tool})
    return out


LAYER_MARK = re.compile(rb"^; layer num/total_layer_count: (\d+)|^M73 L(\d+)")
E_ARG = re.compile(rb"\bE(-?\d*\.?\d+)")


def extrusion(lines):
    """
    ไล่ gcode ทีละบรรทัด (bytes) คืน [ {tool: มม. ที่ฉีดสะสม} ] ตำแหน่ง n = สะสมถึงจบชั้นที่ n (0 = ก่อนชั้นแรก)
    รองรับ E แบบสัมพัทธ์ (M83 ที่ Bambu ใช้) และแบบสัมบูรณ์ (M82 + G92) ถอยเส้นแล้วดันกลับหักล้างกันเอง
    """
    tool, rel, pos, layer = 0, True, 0.0, 0
    cum, snaps = {}, {}
    for raw in lines:
        m = LAYER_MARK.match(raw)
        if m:
            n = int(m.group(1) or m.group(2))
            if n > layer:
                for k in range(layer, n):
                    snaps[k] = dict(cum)
                layer = n
            continue
        code = raw.split(b";", 1)[0].strip()
        if not code:
            continue
        head = code.split(None, 1)[0].upper()
        if head == b"M83":
            rel = True
        elif head == b"M82":
            rel = False
        elif head == b"G92":
            e = E_ARG.search(code)
            if e:
                pos = float(e.group(1))
        elif head[:1] == b"T" and head[1:].isdigit():
            tool = int(head[1:])
        elif head in (b"G0", b"G1", b"G2", b"G3"):
            e = E_ARG.search(code)
            if e:
                v = float(e.group(1))
                d = v if rel else v - pos
                pos = pos + v if rel else v
                cum[tool] = cum.get(tool, 0.0) + d
    snaps[layer] = dict(cum)
    return [snaps.get(k, {}) for k in range(layer + 1)]


def share_at(share, layer):
    """สัดส่วนที่ใช้ไปตอนเครื่องอยู่ชั้น layer: ครึ่งทางของชั้นนั้น (ชั้นที่กำลังปริ้นยังไม่ครบ) ชั้นเกินไฟล์ = ชั้นสุดท้าย"""
    n = min(max(layer, 0), len(share) - 1)
    return share[0] if n == 0 else (share[n - 1] + share[n]) / 2


def curve(fil, layers):
    """เติมสัดส่วนสะสมต่อชั้นให้แต่ละเส้น (ช่อง share) จากผลของ extrusion เส้นที่ gcode ไม่ได้ใช้เลย = ไม่เติม (ใช้สัดส่วนชั้นแทน)"""
    for f in fil:
        total = layers[-1].get(f["tool"], 0.0) if layers else 0.0
        if total > 0:
            f["share"] = [round(max(0.0, min(1.0, s.get(f["tool"], 0.0) / total)), 4) for s in layers]
    return fil


def slice_info(x, name, gcode_file, plate=None):
    """ดึงไฟล์งานจากเครื่อง คืน (ข้อความ slice_info.config, ผลไล่ gcode ของเพลตนั้น หรือ None) ไม่เจอ = FileNotFoundError บอกที่ที่ลองไป"""
    f = ftps(x)
    try:
        paths = candidates(name, gcode_file)
        if name:
            for folder in ("/cache", "/", "/model"):
                try:
                    paths += [f"{folder.rstrip('/')}/{n.rsplit('/', 1)[-1]}" for n in f.nlst(folder) if n.rsplit("/", 1)[-1].startswith(name) and n.lower().endswith(".3mf")]
                except ftplib.all_errors:
                    continue
        for path in dict.fromkeys(paths):
            with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as buf:
                size = [0]

                def write(chunk, buf=buf, size=size):
                    size[0] += len(chunk)
                    if size[0] > MAX_FILE:
                        raise ValueError("file too large")
                    buf.write(chunk)

                try:
                    f.retrbinary(f"RETR {path}", write)
                except (*ftplib.all_errors, ValueError):
                    continue
                buf.seek(0)
                try:
                    with zipfile.ZipFile(buf) as z:
                        text = z.read("Metadata/slice_info.config").decode("utf-8", errors="replace")
                        gcodes = sorted(n for n in z.namelist() if re.fullmatch(r"Metadata/plate_\d+\.gcode", n))
                        g = f"Metadata/plate_{plate}.gcode" if plate and f"Metadata/plate_{plate}.gcode" in gcodes else (gcodes[0] if gcodes else None)
                        if not g:
                            return text, None
                        with z.open(g) as fh:
                            return text, extrusion(fh)
                except (zipfile.BadZipFile, KeyError):
                    continue
        # บอกว่าลองที่ไหนไปบ้าง ให้คนดู log แล้วรู้ว่าไฟล์งานอยู่ที่อื่น
        raise FileNotFoundError(f"ไม่เจอไฟล์งาน ลอง {list(dict.fromkeys(paths))}")
    finally:
        try:
            f.quit()
        except ftplib.all_errors:
            pass


def fraction(p):
    """สัดส่วนที่ปริ้นไปแล้ว 0 ถึง 1 จากชั้น ไม่มีชั้นใช้ %"""
    layer, total = p.get("layer_num"), p.get("total_layer_num")
    if isinstance(layer, int) and isinstance(total, int) and total > 0:
        return max(0.0, min(1.0, layer / total))
    pct = p.get("pct")
    return max(0.0, min(1.0, pct / 100)) if isinstance(pct, int) else 0.0


class Tracker:
    """ตามงานของทุกเครื่อง step() ทุกรอบ คืนงานที่เพิ่งจบ fetch = ฟังก์ชันดึง slice_info (ทดสอบแทนได้) None = ไม่ดึง (แบบผ่านบัญชี)"""

    def __init__(self, fetch=slice_info):
        self.fetch = fetch
        self.cur = {}

    def grams(self, pid):
        """กรัมทั้งงานที่กำลังปริ้น (ไว้โชว์ตอนกดดูเครื่อง) ยังไม่รู้ = None"""
        c = self.cur.get(pid)
        return round(sum(x["grams"] for x in c["fil"]), 1) if c and c.get("fil") else None

    def used(self, pid, layer):
        """กรัมที่ใช้ไปแล้วของงานที่กำลังปริ้น ถึงชั้น layer (จาก gcode) ยังไม่รู้ = None"""
        c = self.cur.get(pid)
        if not c or not c.get("fil") or not all(f.get("share") for f in c["fil"]):
            return None
        return round(sum(f["grams"] * share_at(f["share"], layer) for f in c["fil"]), 1)

    def _start(self, pid, x, p, key, now):
        c = {"key": key, "job": str(p.get("subtask_name") or "")[:80], "started": int(now), "frac": 0.0, "layer": 0, "fil": None}
        self.cur[pid] = c
        if self.fetch and x.get("host") and x.get("access_code"):
            name, gfile, plate = c["job"], p.get("gcode_file"), plate_of(p.get("gcode_file"))

            def run():
                try:
                    text, layers = self.fetch(x, name, gfile, plate)
                    fil = parse_slice(text, plate) if text else None
                    c["fil"] = curve(fil, layers) if fil and layers else fil
                except Exception as e:  # noqa: BLE001 ดึงไม่ได้ = งานนี้ไม่รู้กรัม ตัวส่งต้องทำงานต่อ
                    print(f"{pid}: ดึงไฟล์งานไม่ได้ {e!r}", flush=True)

            threading.Thread(target=run, daemon=True).start()

    def _finish(self, pid, result, now):
        c = self.cur.pop(pid)
        frac = 1.0 if result == "done" else c["frac"]

        def used(f):
            # ไม่เสร็จ: สัดส่วนเส้นที่ฉีดไปถึงจบชั้นที่ปริ้นถึง (gcode) ไม่มีก็สัดส่วนชั้น
            share = f.get("share")
            part = 1.0 if result == "done" else share_at(share, c["layer"]) if share else frac
            return round(f["grams"] * part, 2)

        fil = [{"type": f["type"], "color": f["color"], "grams": f["grams"], "used": used(f)} for f in c["fil"] or []]
        # key เป็น ASCII เสมอ (ชื่องานภาษาไทยอยู่ในช่อง job) งานเดียวกันส่งซ้ำได้ เว็บไม่บันทึกซ้ำ
        key = hashlib.sha1(c["key"].encode()).hexdigest()[:16]
        return {"printer": pid, "key": f"{key}@{c['started']}", "job": c["job"], "started": c["started"], "ended": int(now), "result": result, "pct": round(frac * 100), "filaments": fil, "known": c["fil"] is not None}

    def step(self, pid, x, p, now):
        state = p.get("gcode_state")
        done = []
        c = self.cur.get(pid)
        if state in ACTIVE:
            key = f"{p.get('task_id', '')}-{p.get('subtask_id', '')}-{p.get('subtask_name', '')}"
            if c and c["key"] != key:
                done.append(self._finish(pid, "stopped", now))
                c = None
            if not c:
                self._start(pid, x, p, key, now)
                c = self.cur[pid]
            c["frac"] = max(c["frac"], fraction(p))
            if isinstance(p.get("layer_num"), int):
                c["layer"] = max(c["layer"], p["layer_num"])
        elif c and state in ("FINISH", "FAILED", "IDLE"):
            done.append(self._finish(pid, {"FINISH": "done", "FAILED": "failed"}.get(state, "stopped"), now))
        return done
