"""สร้าง bambu.json แบบผ่านบัญชี Bambu (mode cloud) ให้ bambu.py ใช้ รันครั้งเดียวฝั่งแล็บ

  python3 bambu_setup.py [bambu.json] [IX-3D-X1E-]

ถามอีเมลกับรหัสผ่านบัญชี Bambu ที่ผูกเครื่องไว้ (บัญชีของแล็บ) ถ้าบัญชีขอรหัสยืนยันทางอีเมลจะถามต่อ
ถ้าเครื่องนี้ล็อกอิน Bambu Studio ค้างไว้ จะถามว่าใช้บัญชีนั้นเลยไหม (ไม่ต้องพิมพ์รหัส)
รหัสผ่านไม่ถูกเก็บ ในไฟล์มีแค่ uid กับ token (อายุราว 3 เดือน หมดแล้วรันไฟล์นี้ใหม่) ไฟล์ตั้งสิทธิ์อ่านได้เฉพาะเจ้าของเครื่อง
เครื่องที่ผูกบัญชีจับคู่กับรหัสบนเว็บจากเลขในชื่อเครื่อง (X1E-03 = IX-3D-X1E-03) ไม่มีเลขก็เรียงตามชื่อ เปิดไฟล์แก้เองได้
ใช้แค่ stdlib

ponytail: ขั้นล็อกอินเขียนตามที่ ha-bambulab ใช้กับ API ของ Bambu ที่ไม่ได้เปิดเป็นทางการ ยังไม่ได้ลองกับบัญชีจริง
  บัญชีที่เปิดยืนยันสองขั้นแบบแอป (TFA) ยังไม่รองรับ
"""

import getpass
import json
import os
import re
import sys
import urllib.error
import urllib.request

API = {"us": "https://api.bambulab.com", "cn": "https://api.bambulab.cn"}


class ApiError(Exception):
    pass


def call(url, body=None, token=None, quiet=False):
    """quiet = พังแล้ว raise ApiError แทนปิดโปรแกรม (ใช้ตอนลอง token ที่ไม่รู้ว่ายังใช้ได้ไหม)"""
    headers = {"Content-Type": "application/json", "User-Agent": "teewang-agent/1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), headers=headers, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        msg = f"{url} ตอบ {e.code}: {e.read()[:300].decode(errors='replace')}"
        if quiet:
            raise ApiError(msg) from e
        sys.exit(msg)


def assign(devices, prefix="IX-3D-X1E-"):
    """[(รหัสบนเว็บ, เครื่อง)] เลขในชื่อเครื่องก่อน ชื่อไหนไม่มีเลขหรือเลขซ้ำ ใช้เลขที่ยังว่างเรียงตามชื่อ"""
    taken, out, rest = set(), [], []
    for d in sorted(devices, key=lambda d: d.get("name") or ""):
        m = re.findall(r"\d+", d.get("name") or "")
        n = int(m[-1]) if m else 0
        if 1 <= n <= 99 and n not in taken:
            taken.add(n)
            out.append((n, d))
        else:
            rest.append(d)
    free = (n for n in range(1, 100) if n not in taken)
    out += [(next(free), d) for d in rest]
    return [(f"{prefix}{n:02d}", d) for n, d in sorted(out, key=lambda x: x[0])]


# ที่ Bambu Studio เก็บการล็อกอินไว้ (Windows, Mac, Linux)
STUDIO = [
    os.path.join(os.environ.get("APPDATA", ""), "BambuStudio", "BambuNetworkEngine.conf"),
    os.path.expanduser("~/Library/Application Support/BambuStudio/BambuNetworkEngine.conf"),
    os.path.expanduser("~/.config/BambuStudio/BambuNetworkEngine.conf"),
]


def tokens(data, key=""):
    """ไล่หาค่าที่ชื่อมีคำว่า token (ไม่เอา refresh) ทุกชั้น ไม่ผูกกับโครงไฟล์ของ Bambu Studio รุ่นใดรุ่นหนึ่ง"""
    if isinstance(data, dict):
        for k, v in data.items():
            yield from tokens(v, str(k))
    elif isinstance(data, list):
        for v in data:
            yield from tokens(v, key)
    elif isinstance(data, str) and "token" in key.lower() and "refresh" not in key.lower() and len(data) >= 20:
        yield data


def studio_token(api):
    """token จาก Bambu Studio ที่ล็อกอินค้างไว้ในเครื่องนี้ ที่ลองเรียก Bambu แล้วใช้ได้จริง หรือ None
    ponytail: ยังไม่เคยเห็นไฟล์จริง ถ้า Bambu Studio เข้ารหัสไฟล์ไว้ จะหาไม่เจอแล้วไปถามรหัสผ่านแทน"""
    for path in STUDIO:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        for tok in tokens(data):
            try:
                if call(f"{api}/v1/design-user-service/my/preference", token=tok, quiet=True).get("uid"):
                    return tok
            except (ApiError, OSError, ValueError):
                continue
    return None


def login(api):
    email = input("อีเมลบัญชี Bambu: ").strip()
    r = call(f"{api}/v1/user-service/user/login", {"account": email, "password": getpass.getpass("รหัสผ่าน (ไม่แสดงตอนพิมพ์): "), "apiError": ""})
    if r.get("accessToken"):
        return r["accessToken"]
    if r.get("loginType") == "verifyCode":
        call(f"{api}/v1/user-service/user/sendemail/code", {"email": email, "type": "codeLogin"})
        r = call(f"{api}/v1/user-service/user/login", {"account": email, "code": input("รหัสยืนยันที่ส่งไปทางอีเมล: ").strip()})
        if r.get("accessToken"):
            return r["accessToken"]
    if r.get("loginType") == "tfa":
        sys.exit("บัญชีนี้เปิดยืนยันสองขั้นแบบแอป ตัวช่วยนี้ยังไม่รองรับ")
    sys.exit(f"ล็อกอินไม่ผ่าน: {json.dumps(r, ensure_ascii=False)[:300]}")


def main(path, prefix):
    region = "cn" if input("บัญชีจีน (bambulab.cn) ใช่ไหม [y/N]: ").strip().lower() == "y" else "us"
    api = API[region]
    token = studio_token(api)
    if token and input("เจอบัญชีที่ล็อกอินค้างใน Bambu Studio เครื่องนี้ ใช้บัญชีนั้นเลยไหม [Y/n]: ").strip().lower() == "n":
        token = None
    token = token or login(api)
    uid = call(f"{api}/v1/design-user-service/my/preference", token=token)["uid"]
    devices = call(f"{api}/v1/iot-service/api/user/bind", token=token).get("devices", [])
    if not devices:
        sys.exit("บัญชีนี้ไม่มีเครื่องที่ผูกไว้")
    pairs = assign(devices, prefix)
    for pid, d in pairs:
        print(f"{pid}  <-  {d.get('name')}  ({d.get('dev_product_name') or d.get('dev_model_name')}, {'ออนไลน์' if d.get('online') else 'ออฟไลน์'})")
    cfg = {
        "mode": "cloud",
        "cloud": {"region": region, "uid": str(uid), "token": token},
        "printers": [{"id": pid, "model": d.get("dev_product_name") or "", "serial": d["dev_id"]} for pid, d in pairs],
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"เขียน {path} แล้ว ({len(pairs)} เครื่อง) เช็กคู่ข้างบนว่าตรงป้ายบนเครื่องไหม ไม่ตรงแก้ id ในไฟล์ได้")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "bambu.json", sys.argv[2] if len(sys.argv) > 2 else "IX-3D-X1E-")
