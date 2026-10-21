"""สำรวจเครื่อง Bambu แบบ LAN ครั้งเดียว ก่อนทำส่วนนับกรัมเส้นต่องาน: อ่านอย่างเดียว ไม่เขียนอะไรลงเครื่อง

  TeewangBambu.exe probe        (หรือ python3 bambu_probe.py bambu.json)

พิมพ์ให้ดูว่า
  1. รายงานสถานะของแต่ละเครื่องมีช่องเกี่ยวกับงานอะไรบ้าง (ชื่องาน ไฟล์ task_id ชั้น) กับเส้นใน AMS
  2. เข้าที่เก็บไฟล์ของเครื่อง (FTPS พอร์ต 990 ด้วย access code) ได้ไหม ไฟล์งานอยู่โฟลเดอร์ไหน
  3. ใน Metadata/slice_info.config ของไฟล์งานที่กำลังปริ้นมีกรัมเส้นยังไง
ใช้แค่ LIST/NLST/RETR ไม่ STOR DELE หรือเปลี่ยนชื่อ ไฟล์งานอ่านในหน่วยความจำ ไม่เก็บ ไม่ส่งไปไหน
access code ไม่ถูกพิมพ์ออกมา
"""

import ftplib
import io
import json
import re
import ssl
import sys
import threading
import zipfile

JOB_KEYS = ["gcode_state", "mc_percent", "mc_remaining_time", "subtask_name", "gcode_file", "task_id", "subtask_id", "project_id", "profile_id", "print_type", "layer_num", "total_layer_num", "mc_print_stage", "stg_cur"]


def report(x, wait=15):
    """รายงานเต็ม (pushall) ของเครื่องเดียว คืน dict ของช่อง print หรือ {"error": ...}"""
    import paho.mqtt.client as mqtt

    got, done = {}, threading.Event()
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv311)
    c.username_pw_set("bblp", x["access_code"])
    c.tls_set(cert_reqs=ssl.CERT_NONE)
    c.tls_insecure_set(True)

    def on_connect(cl, _u, _f, rc, _p):
        if rc.is_failure:
            got["error"] = str(rc)
            done.set()
            return
        cl.subscribe(f"device/{x['serial']}/report")
        cl.publish(f"device/{x['serial']}/request", json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))

    def on_message(_cl, _u, msg):
        try:
            pr = json.loads(msg.payload).get("print")
        except ValueError:
            return
        if isinstance(pr, dict) and "gcode_state" in pr:
            got.update(pr)
            done.set()

    c.on_connect, c.on_message = on_connect, on_message
    c.connect_async(x["host"], x.get("port", 8883))
    c.loop_start()
    if not done.wait(wait):
        got.setdefault("error", "ไม่มีรายงานใน 15 วินาที")
    c.loop_stop()
    c.disconnect()
    return got


def ams_lines(pr):
    """เส้นใน AMS: ช่อง ชนิด สี เหลือ % และช่องที่ใช้อยู่"""
    ams = pr.get("ams") if isinstance(pr.get("ams"), dict) else {}
    out = [f"    ช่องที่ใช้อยู่ tray_now={ams.get('tray_now')!r}"]
    for unit in ams.get("ams", []) if isinstance(ams.get("ams"), list) else []:
        for tr in unit.get("tray", []) if isinstance(unit, dict) else []:
            if isinstance(tr, dict) and tr.get("tray_type"):
                out.append(f"    AMS {unit.get('id')} ช่อง {tr.get('id')}: {tr.get('tray_type')} สี {tr.get('tray_color')} เหลือ {tr.get('remain')}% ({tr.get('tray_sub_brands') or ''})")
    vt = pr.get("vt_tray")
    if isinstance(vt, dict) and vt.get("tray_type"):
        out.append(f"    ช่องนอก AMS: {vt.get('tray_type')} สี {vt.get('tray_color')}")
    return out


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


def ftps(x):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    f = ImplicitFTPS(context=ctx)
    f.connect(x["host"], 990, timeout=15)
    f.login("bblp", x["access_code"])
    f.prot_p()
    return f


def slice_lines(raw):
    """บรรทัด <plate> <metadata> <filament> จาก Metadata/slice_info.config (ข้อมูลตอนสไลซ์ ไม่มีโมเดล)"""
    z = zipfile.ZipFile(io.BytesIO(raw))
    names = [n for n in z.namelist() if n.startswith("Metadata/")]
    out = ["    ไฟล์ใน Metadata: " + ", ".join(n[9:] for n in names[:25])]
    if "Metadata/slice_info.config" in names:
        for line in z.read("Metadata/slice_info.config").decode(errors="replace").splitlines():
            if re.search(r"<(plate|filament|metadata|warning|object)\b", line):
                out.append("    " + line.strip()[:200])
    else:
        out.append("    ไม่มี Metadata/slice_info.config")
    return out[:60]


def files(f, folder):
    try:
        return [n.rsplit("/", 1)[-1] for n in f.nlst(folder)]
    except ftplib.all_errors as e:
        return [f"(อ่าน {folder} ไม่ได้: {e})"]


def main(path):
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    if cfg.get("mode") != "lan":
        sys.exit("probe ใช้กับแบบต่อตรงในวง (LAN) เท่านั้น")
    printers = cfg["printers"]
    printing = []
    print("== 1. รายงานสถานะ")
    for x in printers:
        pr = report(x)
        print(f"{x['id']} ({x['host']})")
        if "error" in pr:
            print(f"    ต่อไม่ได้: {pr['error']}")
            continue
        for k in JOB_KEYS:
            if k in pr:
                v = pr[k]
                print(f"    {k} = {str(v)[:60]!r}")
        print(f"    ช่องทั้งหมดในรายงาน {len(pr)} ช่อง: {', '.join(sorted(pr)[:80])}")
        print("\n".join(ams_lines(pr)))
        if pr.get("gcode_state") in ("RUNNING", "PAUSE", "PREPARE") and len(printing) < 2:
            printing.append((x, pr))
    print("\n== 2-3. ที่เก็บไฟล์ของเครื่องที่กำลังปริ้น (อ่านอย่างเดียว)")
    if not printing:
        print("ไม่มีเครื่องกำลังปริ้น ให้รัน probe ใหม่ตอนมีงานปริ้นอยู่")
    for x, pr in printing:
        print(f"{x['id']}")
        try:
            f = ftps(x)
        except (OSError, ftplib.all_errors) as e:
            print(f"    เข้า FTPS ไม่ได้: {e!r}")
            continue
        print("    เข้า FTPS ได้")
        name = str(pr.get("subtask_name") or "")
        hit = None
        for folder in ("/", "/cache", "/model"):
            ls = files(f, folder)
            match = [n for n in ls if name and n.startswith(name)]
            print(f"    {folder}: {len(ls)} รายการ ไฟล์ .3mf {sum(n.endswith('.3mf') for n in ls)} ตรงชื่องาน {match[:3]}")
            if match and not hit:
                hit = f"{folder.rstrip('/')}/{match[0]}"
        if hit:
            buf = io.BytesIO()
            try:
                f.retrbinary(f"RETR {hit}", buf.write)
                print(f"    อ่าน {hit} ได้ {buf.tell()} ไบต์")
                print("\n".join(slice_lines(buf.getvalue())))
            except (OSError, ftplib.all_errors, zipfile.BadZipFile) as e:
                print(f"    อ่านไฟล์งานไม่ได้: {e!r}")
        try:
            f.quit()
        except (OSError, ftplib.all_errors):
            pass


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "bambu.json")
