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


# โฟลเดอร์ที่ Bambu Studio เก็บค่าไว้ (Windows, Mac, Linux) ไล่ทุกไฟล์ .conf .json ชั้นบนสุด
STUDIO_DIRS = [
    os.path.join(os.environ.get("APPDATA", ""), "BambuStudio"),
    os.path.expanduser("~/Library/Application Support/BambuStudio"),
    os.path.expanduser("~/.config/BambuStudio"),
]
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def studio_files():
    for d in STUDIO_DIRS:
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for n in names:
            if n.endswith((".conf", ".json")):
                path = os.path.join(d, n)
                try:
                    with open(path, "rb") as f:
                        yield path, parse(f.read())
                except OSError:
                    continue


def parse(raw):
    """JSON ของ Bambu Studio มีบรรทัด # (เช่น # MD5 checksum) ต่อท้าย ตัดทิ้งก่อนอ่าน อ่านไม่ได้คืนคำอธิบายไฟล์ (str) ไม่มีเนื้อหา"""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return f"ไม่ใช่ข้อความ น่าจะเข้ารหัสไว้ ยาว {len(raw)} ไบต์"
    try:
        return json.loads("\n".join(x for x in text.splitlines() if not x.lstrip().startswith("#")))
    except ValueError:
        first = text.lstrip()[:1]
        return f"เป็นข้อความแต่ไม่ใช่ JSON ยาว {len(raw)} ไบต์ ขึ้นต้นด้วย {'ตัวอักษร' if first.isalnum() else repr(first)} มีคำว่า token {text.lower().count('token')} ที่"


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


def shape(data, path=""):
    """บรรทัดบอกโครงไฟล์ให้คนส่งมาให้ดู: ชื่อช่องที่เกี่ยวกับบัญชีกับความยาวค่า ไม่โชว์ค่า ยกเว้นอีเมล"""
    if isinstance(data, dict):
        for k, v in data.items():
            yield from shape(v, f"{path}.{k}")
    elif isinstance(data, list):
        for i, v in enumerate(data[:5]):
            yield from shape(v, f"{path}[{i}]")
    elif re.search(r"token|user|uid|account|email|login|region|country", path, re.I):
        v = str(data)
        yield f"  {path} = {v}" if EMAIL.match(v) else f"  {path} ({type(data).__name__} ยาว {len(v)})"


def studio_token():
    """(region, token) จาก Bambu Studio ที่ล็อกอินค้างไว้ในเครื่องนี้ ลองเรียก Bambu แล้วใช้ได้จริง หรือ None
    ponytail: ยังไม่เคยเห็นไฟล์จริง หาไม่เจอจะพิมพ์โครงไฟล์ (ไม่มีค่า token) ให้ส่งมาดูแล้วแก้ให้ตรง"""
    found = list(studio_files())
    for _, data in found:
        for tok in tokens(data) if not isinstance(data, str) else []:
            for region, api in API.items():
                try:
                    if call(f"{api}/v1/design-user-service/my/preference", token=tok, quiet=True).get("uid"):
                        return region, tok
                except (ApiError, OSError, ValueError):
                    continue
    print("ไม่เจอการล็อกอินที่ใช้ได้ของ Bambu Studio ในเครื่องนี้" if found else "ไม่เจอโฟลเดอร์ของ Bambu Studio ในเครื่องนี้")
    for path, data in found:
        lines = [f"  ({data})"] if isinstance(data, str) else list(shape(data))
        print(path)
        print("\n".join(lines) or "  (ไม่มีช่องเกี่ยวกับบัญชี)")
    if found:
        print("แคปข้อความข้างบนส่งให้ผู้ดูแลเว็บได้ ไม่มีค่า token หรือรหัสผ่านอยู่ในนั้น")
    return None


def login():
    """อีเมลกับรหัสผ่าน ลองบัญชีทั่วโลกก่อน ไม่ผ่านค่อยลองบัญชีจีน คืน (region, token)"""
    email = input("อีเมลบัญชี Bambu: ").strip()
    pw = getpass.getpass("รหัสผ่าน (ไม่แสดงตอนพิมพ์): ")
    errors = []
    for region, api in API.items():
        try:
            r = call(f"{api}/v1/user-service/user/login", {"account": email, "password": pw, "apiError": ""}, quiet=True)
        except (ApiError, OSError) as e:
            errors.append(str(e))
            continue
        if r.get("accessToken"):
            return region, r["accessToken"]
        if r.get("loginType") == "verifyCode":
            call(f"{api}/v1/user-service/user/sendemail/code", {"email": email, "type": "codeLogin"})
            r = call(f"{api}/v1/user-service/user/login", {"account": email, "code": input("รหัสยืนยันที่ส่งไปทางอีเมล: ").strip()})
            if r.get("accessToken"):
                return region, r["accessToken"]
        if r.get("loginType") == "tfa":
            sys.exit("บัญชีนี้เปิดยืนยันสองขั้นแบบแอป ตัวช่วยนี้ยังไม่รองรับ")
        errors.append(json.dumps(r, ensure_ascii=False)[:300])
    sys.exit("ล็อกอินไม่ผ่าน:\n" + "\n".join(errors))


def main(path, prefix):
    got = studio_token()
    if got and input("เจอบัญชีที่ล็อกอินค้างใน Bambu Studio เครื่องนี้ ใช้บัญชีนั้นเลยไหม [Y/n]: ").strip().lower() == "n":
        got = None
    region, token = got or login()
    api = API[region]
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
