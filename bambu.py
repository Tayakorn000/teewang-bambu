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
  id = ต้องตรงกับรายชื่อเครื่องบนเว็บ เช่น INNO-X: IX-3D-X1E-01 ถึง IX-3D-X1E-10, SIT: SIT-3D-X2D-01
  model = รุ่น (X1E, A1, ...) หน้าเว็บวาดรุ่น A เป็นโครงเปิด ที่เหลือเป็นกล่องปิด ไม่ใส่ก็ได้
  X1E มีช่องสาย LAN และ Wi-Fi แบบ WPA2-Enterprise (ตามสเปกของ Bambu) A1 ใช้ Wi-Fi ที่ใช้รหัสผ่านธรรมดา

ponytail: ไม่ได้ลองกับเครื่องจริงเลย ข้อความจากเครื่องเขียนตามรูปแบบที่คนอื่นแกะไว้ (OpenBambuAPI, ha-bambulab)
  ต้องลองกับรุ่นและเฟิร์มแวร์ของแล็บก่อนใช้จริง
"""

import json
import ssl
import sys
import threading
import time

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
    return p


def item(pid, p, now, connected=True, model=None):
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
    if cfg.get("mode", "lan") == "cloud":
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
            items = [item(pid, known[pid], now, up.get(pid, False), model[pid]) for pid in known]
        ok = push(items)
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
