"""อ่านสถานะเครื่อง Bambu Lab แล้วส่งแค่สถานะ + เวลาที่เหลือเข้า teewang ทุก 30 วิ

รันฝั่งแล็บ (เครื่องไหนก็ได้ที่เปิดทิ้งไว้) access code / token อยู่ในเครื่องนี้เท่านั้น ไม่ส่งออกไปไหน
อ่านอย่างเดียว ไม่สั่งเครื่อง ไม่ต้องเปิด Developer Mode

  pip install "paho-mqtt>=2,<3"          (EPL-2.0 / BSD-3-Clause)
  python3 bambu_setup.py                 สร้าง bambu.json แบบผ่านบัญชี Bambu ให้ (ถามอีเมล รหัสผ่าน)
  TEEWANG_KEY=... python3 bambu.py bambu.json

bambu.json
  {
    "mode": "lan",                       lan = ต่อเครื่องตรงในวงเน็ตเดียวกัน, cloud = ผ่านบัญชี Bambu ของแล็บ
    "printers": [
      {"id": "IX-3D-X1E-01", "model": "X1E", "serial": "03W00X...", "host": "192.168.1.20", "access_code": "12345678"}
    ],
    "cloud": {"region": "us", "uid": "123456789", "token": "..."}     ใช้เมื่อ mode = cloud (host/access_code ไม่ต้องใส่)
  }
  id = ต้องตรงกับรายชื่อเครื่องใน lib/feeds.ts (fleet) เช่น INNO-X: IX-3D-X1E-01 ถึง IX-3D-X1E-10, SIT: SIT-3D-X2D-01
  model = รุ่น (X1E, A1, ...) หน้าเว็บวาดรุ่น A เป็นโครงเปิด ที่เหลือเป็นกล่องปิด ไม่ใส่ก็ได้
  X1E มีช่องสาย LAN และ Wi-Fi แบบ WPA2-Enterprise (ตามสเปกของ Bambu) A1 ใช้ Wi-Fi ที่ใช้รหัสผ่านธรรมดา

ponytail: ไม่ได้ลองกับเครื่องจริงเลย ข้อความจากเครื่องเขียนตามรูปแบบที่คนอื่นแกะไว้ (OpenBambuAPI, ha-bambulab)
  ต้องลองกับรุ่นและเฟิร์มแวร์ของแล็บก่อนใช้จริง
"""

import json
import re
import ssl
import sys
import threading
import time

from bambu_jobs import Tracker
from push import push

PUSH_S = 30
PUSHALL_S = 300  # ขอสถานะเต็มทุก 5 นาที (Bambu เตือนว่าขอถี่กว่านี้ทำ P1 ช้า)
OFFLINE_S = 360  # ไม่มีข้อความเลยเกินนี้ = ออฟไลน์
# เครื่องค้างสถานะ FINISH/FAILED จนกว่าจะเริ่มงานใหม่ เครื่องไม่รู้ว่าเก็บงานออกไปแล้ว
# ponytail: เกินเวลานี้นับว่าว่าง เดาเอา ถ้าแล็บมีวิธีบอกว่าเก็บงานแล้วค่อยเปลี่ยน
DONE_AS_IDLE_S = 30 * 60

THAI = {"idle": "ว่าง", "printing": "กำลังปริ้น", "paused": "พัก", "done": "เสร็จ", "error": "ผิดพลาด", "offline": "ออฟไลน์"}
STATE = {"IDLE": "idle", "PREPARE": "printing", "SLICING": "printing", "RUNNING": "printing", "PAUSE": "paused", "FINISH": "done", "FAILED": "error"}


def merge(prev, payload, now):
    """รวมข้อความใหม่เข้ากับที่รู้อยู่ (P1/A1 ส่งมาแค่ช่องที่เปลี่ยน) คืน dict ใหม่"""
    p = dict(prev)
    p["seen"] = now
    pr = payload.get("print") if isinstance(payload, dict) else None
    if isinstance(pr, dict):
        g = pr.get("gcode_state")
        if isinstance(g, str) and g != p.get("gcode_state"):
            p["gcode_state"], p["since"] = g, now
        t = pr.get("mc_remaining_time")
        if isinstance(t, int) and t >= 0:
            p["left"] = t
        pc = pr.get("mc_percent")
        if isinstance(pc, int) and 0 <= pc <= 100:
            p["pct"] = pc
        # รายละเอียดตอนกดดูเครื่อง เก็บค่าดิบล่าสุดไว้ แปลงตอนส่ง (detail)
        for k in ("nozzle_temper", "bed_temper", "chamber_temper", "layer_num", "total_layer_num", "subtask_name", "print_error", "hms", "task_id", "subtask_id", "gcode_file"):
            if k in pr:
                p[k] = pr[k]
        ams = pr.get("ams")
        if isinstance(ams, dict):
            if isinstance(ams.get("ams"), list):
                p["ams"] = ams["ams"]
            if "tray_now" in ams:
                p["tray_now"] = ams["tray_now"]
        if isinstance(pr.get("vt_tray"), dict):
            p["vt_tray"] = pr["vt_tray"]
    return p


def bambu_layer(p):
    return p["layer_num"] if isinstance(p.get("layer_num"), int) else 0


def num(v, lo, hi):
    """อุณหภูมิกับเลขชั้นที่เครื่องส่งมา (บางรุ่นเป็นทศนิยมหรือข้อความ) เป็นจำนวนเต็มในช่วงที่เว็บรับ หรือ None"""
    try:
        n = round(float(v))
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


def hexcode(n, parts):
    return "-".join(f"{(n >> (16 * i)) & 0xFFFF:04X}" for i in reversed(range(parts)))


def tray(slot, t):
    ty = re.sub(r"[^A-Za-z0-9+\-_ .]", "", str(t.get("tray_type") or "")).strip()[:16]
    if not ty:
        return None
    color = str(t.get("tray_color") or "")[:6].upper()
    remain = t.get("remain")
    return {"slot": slot, "type": ty, "color": color if len(color) == 6 and all(c in "0123456789ABCDEF" for c in color) else None, "remain": remain if isinstance(remain, int) and 0 <= remain <= 100 else None}


def detail(p):
    """ช่องรายละเอียดตามรูปแบบที่เว็บรับ (lib/feeds.ts printerDetail) ค่าที่แปลกไม่ส่ง"""
    out = {}
    for src, dst, lo, hi in (("nozzle_temper", "nozzle", 0, 500), ("bed_temper", "bed", 0, 200), ("chamber_temper", "chamber", 0, 100), ("layer_num", "layer", 0, 100_000), ("total_layer_num", "layers", 0, 100_000)):
        v = num(p.get(src), lo, hi)
        if v is not None:
            out[dst] = v
    if isinstance(p.get("subtask_name"), str) and p["subtask_name"].strip():
        out["job"] = p["subtask_name"].strip()[:80]
    trays = []
    for unit in p.get("ams") or []:
        if not isinstance(unit, dict) or not str(unit.get("id", "")).isdigit() or int(unit["id"]) > 3:
            continue
        for t in unit.get("tray") or []:
            if isinstance(t, dict) and str(t.get("id", "")).isdigit() and int(t["id"]) <= 3 and (x := tray(f"{'ABCD'[int(unit['id'])]}{int(t['id']) + 1}", t)):
                trays.append(x)
    if isinstance(p.get("vt_tray"), dict) and (x := tray("EXT", p["vt_tray"])):
        trays.append(x)
    if trays:
        out["trays"] = trays
    # tray_now: 0-15 = AMS ตัวที่ (n // 4) ช่อง (n % 4), 254 = ช่องนอก, 255 = ไม่ได้ใช้
    now = str(p.get("tray_now", ""))
    if now.isdigit() and int(now) < 16:
        out["tray_now"] = f"{'ABCD'[int(now) // 4]}{int(now) % 4 + 1}"
    elif now == "254":
        out["tray_now"] = "EXT"
    errors = []
    if isinstance(p.get("print_error"), int) and p["print_error"] > 0:
        errors.append(hexcode(p["print_error"], 2))
    for h in p.get("hms") or []:
        if isinstance(h, dict) and isinstance(h.get("attr"), int) and isinstance(h.get("code"), int):
            errors.append(hexcode((h["attr"] << 32) | h["code"], 4))
    if errors:
        out["errors"] = errors[:8]
    return out


def item(pid, p, now, connected=True, model=None, grams=None, used=None):
    """สถานะที่ส่งเข้าเว็บ ตามรูปแบบของ POST /api/feed"""
    out = {"id": pid}
    if model:
        out["model"] = model
    if not connected or now - p.get("seen", 0) > OFFLINE_S or "gcode_state" not in p:
        return {**out, "state": "offline"}
    state = STATE.get(p["gcode_state"], "idle")
    if state in ("done", "error") and now - p.get("since", now) > DONE_AS_IDLE_S:
        state = "idle"
    out["state"] = state
    out.update(detail(p))
    if grams is not None and state in ("printing", "paused"):
        out["grams"] = grams
        if used is not None:
            out["used"] = min(used, grams)
    if state in ("printing", "paused"):
        if isinstance(p.get("left"), int):
            out["left_min"] = min(p["left"], 10_080)
        if isinstance(p.get("pct"), int):
            out["pct"] = p["pct"]
    return out


def main(path):
    import paho.mqtt.client as mqtt

    cfg = json.load(open(path, encoding="utf-8"))
    printers = cfg["printers"]
    by_serial = {x["serial"]: x["id"] for x in printers}
    model = {x["id"]: x.get("model") for x in printers}
    known = {x["id"]: {} for x in printers}
    up = {}  # client ต่ออยู่ไหม แยกตาม id
    lock = threading.Lock()

    def on_message(client, _u, msg):
        serial = msg.topic.split("/")[1]
        pid = by_serial.get(serial)
        if not pid:
            return
        try:
            payload = json.loads(msg.payload)
        except ValueError:
            return
        with lock:
            known[pid] = merge(known[pid], payload, time.time())

    def make(user, pw, host, port, verify, serials, ids):
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv311)
        c.username_pw_set(user, pw)
        if verify:
            c.tls_set()
        else:  # เครื่องในวง LAN ใช้ใบรับรองที่เซ็นเอง
            c.tls_set(cert_reqs=ssl.CERT_NONE)
            c.tls_insecure_set(True)

        def on_connect(client, _u, _f, rc, _p):
            ok = not rc.is_failure
            for i in ids:
                up[i] = ok
            if not ok:
                print(f"connect {host}: {rc}", file=sys.stderr)
                if "author" in str(rc).lower():
                    print("token หรือ access code ผิดหรือหมดอายุ ตั้งค่าใหม่: TeewangBambu.exe setup หรือ python3 bambu_setup.py", file=sys.stderr)
                return
            for s in serials:
                client.subscribe(f"device/{s}/report")
                client.publish(f"device/{s}/request", json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))

        def on_disconnect(_c, _u, _f, rc, _p):
            for i in ids:
                up[i] = False

        c.on_connect, c.on_disconnect, c.on_message = on_connect, on_disconnect, on_message
        c.reconnect_delay_set(5, 120)
        c.connect_async(host, port)
        c.loop_start()
        return c, serials

    clients = []
    cloud = cfg.get("mode", "lan") == "cloud"
    # นับกรัมต่องานต้องดึงไฟล์จากเครื่องในวง แบบผ่านบัญชีรู้แค่ผลงานกับ % ไม่รู้กรัม
    tracker = Tracker(fetch=None) if cloud else Tracker()
    by_id = {x["id"]: x for x in printers}
    pending = []
    if cloud:
        cl = cfg["cloud"]
        host = {"us": "us.mqtt.bambulab.com", "cn": "cn.mqtt.bambulab.com"}[cl.get("region", "us")]
        clients.append(make(f"u_{cl['uid']}", cl["token"], host, 8883, True, list(by_serial), [x["id"] for x in printers]))
    else:
        for x in printers:
            clients.append(make("bblp", x["access_code"], x["host"], x.get("port", 8883), x.get("tls_verify", False), [x["serial"]], [x["id"]]))

    last_pushall, last = time.time(), ""
    while True:
        time.sleep(PUSH_S)
        now = time.time()
        if now - last_pushall > PUSHALL_S:
            last_pushall = now
            for c, serials in clients:
                for s in serials:
                    c.publish(f"device/{s}/request", json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))
        with lock:
            for pid in known:
                if up.get(pid) and now - known[pid].get("seen", 0) <= OFFLINE_S:
                    for j in tracker.step(pid, by_id[pid], known[pid], now):
                        used = sum(f["used"] for f in j["filaments"])
                        print(time.strftime("%H:%M"), f"{pid} งานจบ ({j['result']} {j['pct']}%) {f'ใช้เส้น {used:.1f} g' if j['known'] else 'ไม่รู้กรัม'}", flush=True)
                        pending.append(j)
            items = [item(pid, known[pid], now, up.get(pid, False), model[pid], tracker.grams(pid), tracker.used(pid, bambu_layer(known[pid]))) for pid in known]
        # งานที่จบแล้วส่งซ้ำจนเว็บรับ (เว็บไม่บันทึกซ้ำ) เก็บไว้ไม่เกิน 200 งานเผื่อเน็ตหลุดนาน
        del pending[:-200]
        ok = push(items, jobs=pending[:50])
        if ok:
            del pending[:50]
        # บอกคนที่เปิดหน้าต่างไว้ เฉพาะตอนสรุปเปลี่ยน ไม่พ่นทุก 30 วิ
        count = {}
        for x in items:
            count[x["state"]] = count.get(x["state"], 0) + 1
        line = f"{'ส่งแล้ว' if ok else 'ส่งไม่ได้'}: " + " ".join(f"{THAI.get(k, k)} {n}" for k, n in sorted(count.items()))
        if line != last:
            print(time.strftime("%H:%M"), line, flush=True)
            last = line


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("python3 bambu.py bambu.json")
    main(sys.argv[1])
