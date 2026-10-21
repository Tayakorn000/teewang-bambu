"""นับกรัมเส้นต่องานของเครื่อง Bambu (ต่อตรงในวง LAN เท่านั้น)

ตอนงานเริ่ม: ดึงไฟล์งาน (.3mf) จากที่เก็บไฟล์ของเครื่อง (FTPS พอร์ต 990 ด้วย access code) อ่าน Metadata/slice_info.config
  ได้กรัมที่สไลซ์ไว้ของแต่ละเส้นในเพลตที่ปริ้น (วิธีเดียวกับ FilaBridge) อ่านในเครื่องนี้ ไม่ส่งไฟล์ไปไหน
ตอนงานจบ: เสร็จ = ใช้ครบตามไฟล์, ล้มเหลว / หยุดกลางทาง = ใช้ไปตามสัดส่วนชั้นที่ปริ้นถึง (ไม่มีชั้นใช้ %) = ส่วนที่เสียไป
  ส่งเป็นรายการงานเข้า teewang พร้อมสถานะเครื่อง (POST /api/feed ช่อง jobs)

ponytail: ยังไม่ได้ลองกับเครื่องจริง ที่อยู่ไฟล์บนเครื่องเดาตามที่คนอื่นแกะไว้ (/cache, ราก, /model) หาไม่เจอ = งานนั้นไม่รู้กรัม
  ส่วนที่เสียเป็นค่าประมาณจากสัดส่วนชั้น ไม่ได้ชั่งจริง ไม่รวมเส้นที่ไล่ทิ้งตอนเปลี่ยนสีระหว่างงานที่ไม่ได้อยู่ในไฟล์
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
        out.append({"type": clean_type(f.get("type")), "color": color if re.fullmatch(r"[0-9A-F]{6}", color) else None, "grams": g})
    return out


def slice_info(x, name, gcode_file):
    """ดึงไฟล์งานจากเครื่องแล้วคืนข้อความ slice_info.config ไม่เจอ = FileNotFoundError บอกที่ที่ลองไป"""
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
                        return z.read("Metadata/slice_info.config").decode("utf-8", errors="replace")
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

    def _start(self, pid, x, p, key, now):
        c = {"key": key, "job": str(p.get("subtask_name") or "")[:80], "started": int(now), "frac": 0.0, "fil": None}
        self.cur[pid] = c
        if self.fetch and x.get("host") and x.get("access_code"):
            name, gfile, plate = c["job"], p.get("gcode_file"), plate_of(p.get("gcode_file"))

            def run():
                try:
                    text = self.fetch(x, name, gfile)
                    c["fil"] = parse_slice(text, plate) if text else None
                except Exception as e:  # noqa: BLE001 ดึงไม่ได้ = งานนี้ไม่รู้กรัม ตัวส่งต้องทำงานต่อ
                    print(f"{pid}: ดึงไฟล์งานไม่ได้ {e!r}", flush=True)

            threading.Thread(target=run, daemon=True).start()

    def _finish(self, pid, result, now):
        c = self.cur.pop(pid)
        frac = 1.0 if result == "done" else c["frac"]
        fil = [{**f, "used": round(f["grams"] * frac, 2)} for f in c["fil"] or []]
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
        elif c and state in ("FINISH", "FAILED", "IDLE"):
            done.append(self._finish(pid, {"FINISH": "done", "FAILED": "failed"}.get(state, "stopped"), now))
        return done
