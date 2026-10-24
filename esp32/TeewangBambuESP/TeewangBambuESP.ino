// ส่งสถานะเครื่องปริ้น Bambu Lab ในวง LAN ขึ้น teewang ทุก 30 วินาที แทนตัวส่งบนโน้ตบุ๊ก
// สถานะอย่างเดียว (ว่าง กำลังปริ้นกี่ % เหลือกี่นาที อุณหภูมิ ชั้น เส้นใน AMS แจ้งเตือน) ไม่นับกรัมเส้นต่องาน
// อ่านอย่างเดียว ไม่สั่งเครื่อง
//
// ตั้งค่าผ่านสาย USB ที่ https://teewang.taokaetech.com/esp/index.html (เลือก bambu.json ของ TeewangBambu.exe + ชื่อ/รหัส Wi-Fi)
// ค่าวิ่งในสายอย่างเดียว ไม่ผ่านอากาศ เก็บใน flash ของ ESP ไม่แสดงกลับออกมา
//
// ต่อเครื่องปริ้นทีละเครื่อง (TLS ครั้งละการเชื่อมต่อเดียว แรม ESP ไม่พอเปิดค้างพร้อมกันทุกเครื่อง) ได้รายงานแล้วตัด ไปเครื่องถัดไป
// ครบรอบส่งขึ้นเว็บทีเดียว
//
// ใช้ PubSubClient 2.8 กับ ArduinoJson 7 (MIT ทั้งคู่) บน esp32 core 3.3

#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <NetworkClientSecure.h>
#include <Preferences.h>
#include <PubSubClient.h>
#include <WiFi.h>

#include "feed.h"

const char *FEED_URL = "https://teewang.taokaetech.com/api/feed";
const char *PUSHALL = "{\"pushing\":{\"sequence_id\":\"0\",\"command\":\"pushall\"}}";
const uint32_t PUSH_MS = 30000;
const uint32_t STUCK_MS = 15 * 60 * 1000;  // ส่งไม่ผ่านนานเกินนี้ รีสตาร์ทตัวเอง (Wi-Fi ค้าง TLS ค้าง แรมแตก)
const int MAX_PRINTERS = 16;

// ใบรับรอง CA ชุดของ Mozilla ที่ core ฝังมาให้ ใช้ตรวจ teewang (Cloudflare เปลี่ยน CA ตอนต่ออายุได้ ชุดใหญ่ไม่พังตาม)
extern const uint8_t ca_start[] asm("_binary_x509_crt_bundle_start");
extern const uint8_t ca_end[] asm("_binary_x509_crt_bundle_end");

struct Printer {
  String id, model, host, serial, code;
  JsonDocument pr;  // ช่อง print ของรายงานเต็มล่าสุด
  long seen = 0, since = 0;
  int fails = 0;
  bool sized = false;
};
Printer printers[MAX_PRINTERS];
int count = 0;
String ssid, pass, key;

NetworkClientSecure tls;
PubSubClient mq(tls);
JsonDocument filter;
Printer *cur = nullptr;
bool got = false;
uint32_t lastRound = 0, lastOk = 0;
String lastLine;

long nowS() { return esp_timer_get_time() / 1000000; }

// พิมพ์ออกทุกช่องทาง: บอร์ดที่มี USB ในตัว (C3 S3) Serial คือ USB, Serial0 คือขา UART
void say(const char *fmt, ...) {
  char b[320];
  va_list a;
  va_start(a, fmt);
  vsnprintf(b, sizeof b, fmt, a);
  va_end(a);
  Serial.println(b);
#if ARDUINO_USB_CDC_ON_BOOT
  Serial0.println(b);
#endif
}

// ค่าตั้งที่เก็บไว้ {"wifi":{"ssid","pass"},"key","printers":[{id,model,host,serial,access_code}]}
// คืนข้อความผิดพลาด หรือ nullptr ถ้าใช้ได้ (apply = ใช้เลย)
const char *useConfig(JsonDocument &c, bool apply) {
  if (!c["wifi"]["ssid"].is<const char *>() || !*c["wifi"]["ssid"].as<const char *>()) return "ไม่มีชื่อ Wi-Fi";
  if (!c["key"].is<const char *>() || !*c["key"].as<const char *>()) return "ไม่มีกุญแจ teewang (teewang_key ใน bambu.json)";
  int n = 0;
  for (JsonObjectConst x : c["printers"].as<JsonArrayConst>()) {
    const char *f[] = {x["id"], x["host"], x["serial"], x["access_code"]};
    if (!f[0] || !f[1] || !f[2] || !f[3] || !*f[0] || !*f[1] || !*f[2] || !*f[3]) return "เครื่องปริ้นต้องมี id host serial access_code (แบบต่อตรงในวงเท่านั้น)";
    if (++n > MAX_PRINTERS) return "เครื่องปริ้นเกิน 16 เครื่อง";
    if (!apply) continue;
    Printer &p = printers[n - 1];
    p = Printer();
    p.id = f[0], p.host = f[1], p.serial = f[2], p.code = f[3], p.model = x["model"] | "";
  }
  if (!n) return "ไม่มีรายชื่อเครื่องปริ้น";
  if (apply) {
    count = n;
    ssid = c["wifi"]["ssid"].as<const char *>(), pass = c["wifi"]["pass"] | "", key = c["key"].as<const char *>();
  }
  return nullptr;
}

void startWifi() {
  WiFi.disconnect();
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(ssid.c_str(), pass.c_str());
  say("กำลังต่อ Wi-Fi %s", ssid.c_str());
}

// รับค่าตั้งเป็น JSON บรรทัดเดียวทางสาย USB (หน้า /esp ส่งมา) เก็บแค่ช่องที่ใช้
void configure(const String &line) {
  JsonDocument in;
  if (deserializeJson(in, line)) return say("ERR อ่านค่าตั้งไม่ได้ (JSON ผิด)");
  JsonDocument c;
  c["wifi"]["ssid"] = in["wifi"]["ssid"];
  c["wifi"]["pass"] = in["wifi"]["pass"];
  c["key"] = in["key"];
  JsonArray a = c["printers"].to<JsonArray>();
  for (JsonObjectConst x : in["printers"].as<JsonArrayConst>()) {
    JsonObject y = a.add<JsonObject>();
    for (const char *k : {"id", "model", "host", "serial", "access_code"}) y[k] = x[k];
  }
  if (const char *err = useConfig(c, false)) return say("ERR %s", err);
  String s;
  serializeJson(c, s);
  Preferences pref;
  pref.begin("teewang");
  bool ok = pref.putString("cfg", s) == s.length();
  pref.end();
  if (!ok) return say("ERR เก็บค่าตั้งไม่ได้ (ยาวเกิน)");
  useConfig(c, true);
  say("OK รับค่าตั้งแล้ว เครื่องปริ้น %d เครื่อง", count);
  lastRound = 0, lastOk = millis(), lastLine = "";
  startWifi();
}

void pollStream(Stream &in, String &buf) {
  while (in.available()) {
    char ch = in.read();
    if (ch == '\n' || ch == '\r') {
      if (buf.startsWith("{")) configure(buf);
      buf = "";
    } else if (buf.length() < 8000) buf += ch;
  }
}

void pollSerial() {
  static String a;
  pollStream(Serial, a);
#if ARDUINO_USB_CDC_ON_BOOT
  static String b;
  pollStream(Serial0, b);
#endif
}

void onReport(char *topic, byte *payload, unsigned int len) {
  if (!cur || got) return;
  JsonDocument d;
  if (deserializeJson(d, payload, len, DeserializationOption::Filter(filter))) return;
  const char *g = d["print"]["gcode_state"];
  if (!g) return;  // ข้อความอื่นที่ไม่ใช่รายงานสถานะ
  const char *old = cur->pr["gcode_state"];
  if (!old || strcmp(old, g)) cur->since = nowS();
  // ponytail: แทนทั้งก้อน X1 ส่งรายงานเต็มทุกครั้ง ถ้าใช้กับ P1/A1 (ส่งมาแค่ช่องที่เปลี่ยน) ต้องรวมทีละช่องแบบ merge() ใน bambu.py
  cur->pr.set(d["print"]);
  cur->seen = nowS();
  got = true;
  if (!cur->sized) {  // ขนาดรายงานจริง ไว้ดูว่าบัฟเฟอร์ 32 KB พอไหม (เกินบัฟเฟอร์ PubSubClient ทิ้งเงียบ)
    say("%s รายงาน %u ไบต์", cur->id.c_str(), len);
    cur->sized = true;
  }
}

// ต่อเครื่องเดียว รอรายงานไม่เกิน 8 วิ แล้วตัด
void readPrinter(Printer &p) {
  cur = &p, got = false;
  mq.setServer(p.host.c_str(), 8883);
  char cid[24];
  snprintf(cid, sizeof cid, "teewang-%08lx", (unsigned long)esp_random());
  if (mq.connect(cid, "bblp", p.code.c_str())) {
    String t = "device/" + p.serial;
    mq.subscribe((t + "/report").c_str());
    // X1 ส่งรายงานเต็มเองเรื่อยๆ รอก่อน 2 วิ ไม่มีค่อยขอ (Bambu เตือนว่าขอถี่ทำ P1 ช้า)
    uint32_t t0 = millis();
    bool asked = false;
    while (!got && mq.connected() && millis() - t0 < 8000) {
      if (!asked && millis() - t0 > 2000) asked = mq.publish((t + "/request").c_str(), PUSHALL);
      mq.loop();
      pollSerial();
      delay(5);
    }
    mq.disconnect();
  }
  int state = mq.state();
  tls.stop();
  cur = nullptr;
  if (got) p.fails = 0;
  else if (++p.fails == 2) {
    // 4 = access code ผิด, -2 = ต่อไม่ติด (ไอพีเปลี่ยน ปิดเครื่อง อยู่คนละวง)
    say("%s (%s) ต่อไม่ได้ %s", p.id.c_str(), p.host.c_str(), state == 4 || state == 5 ? "access code ผิด" : state == -2 ? "ไม่เจอเครื่องที่ไอพีนี้" : "ไม่มีรายงาน");
  }
}

bool push(const String &body) {
  NetworkClientSecure c;
  c.setCACertBundle(ca_start, ca_end - ca_start);
  HTTPClient h;
  if (!h.begin(c, FEED_URL)) return false;
  h.setTimeout(15000);
  h.setUserAgent("teewang-agent/1");
  h.addHeader("Content-Type", "application/json");
  h.addHeader("Authorization", "Bearer " + key);
  int code = h.POST(body);
  if (code != 204) {
    String why = code > 0 ? h.getString().substring(0, 200) : HTTPClient::errorToString(code);
    say("ส่งไม่ได้ %d %s", code, why.c_str());
  }
  h.end();
  return code == 204;
}

void setup() {
  Serial.setRxBufferSize(8192);
  Serial.begin(115200);
#if ARDUINO_USB_CDC_ON_BOOT
  Serial.setTxTimeoutMs(0);  // ไม่ได้เสียบคอม ไม่ต้องรอ
  Serial0.setRxBufferSize(8192);
  Serial0.begin(115200);
#endif
  JsonObject f = filter["print"].to<JsonObject>();
  for (const char *k : {"gcode_state", "mc_percent", "mc_remaining_time", "nozzle_temper", "bed_temper", "chamber_temper", "layer_num", "total_layer_num", "subtask_name", "print_error"}) f[k] = true;
  f["hms"][0]["attr"] = true;
  f["hms"][0]["code"] = true;
  f["ams"]["tray_now"] = true;
  JsonObject u = f["ams"]["ams"][0].to<JsonObject>();
  u["id"] = true;
  for (const char *k : {"id", "tray_type", "tray_color", "remain"}) u["tray"][0][k] = true;
  for (const char *k : {"tray_type", "tray_color", "remain"}) f["vt_tray"][k] = true;

  tls.setInsecure();  // เครื่องปริ้นใช้ใบรับรองที่เซ็นเอง อยู่ในวงเดียวกัน (ตัวส่งบนโน้ตบุ๊กก็ไม่ตรวจ)
  tls.setConnectionTimeout(5000);
  tls.setHandshakeTimeout(10);
  mq.setSocketTimeout(10);
  mq.setCallback(onReport);
  if (!mq.setBufferSize(32768)) say("แรมไม่พอสำหรับบัฟเฟอร์ 32 KB");

  Preferences pref;
  pref.begin("teewang", true);
  String s = pref.getString("cfg", "");
  pref.end();
  JsonDocument c;
  if (s.length() && !deserializeJson(c, s) && !useConfig(c, true)) startWifi();
  lastOk = millis();
}

void loop() {
  pollSerial();
  if (!count) {
    static uint32_t t;
    if (millis() - t > 5000) say("ยังไม่ได้ตั้งค่า เปิด https://teewang.taokaetech.com/esp/index.html บนคอมที่เสียบ ESP อยู่"), t = millis();
    return delay(20);
  }
  if (millis() - lastOk > STUCK_MS) {
    say("ส่งไม่ผ่านมา 15 นาที รีสตาร์ท");
    delay(100);
    ESP.restart();
  }
  if (WiFi.status() != WL_CONNECTED) {
    static uint32_t t;
    if (millis() - t > 10000) say("ยังต่อ Wi-Fi %s ไม่ได้", ssid.c_str()), t = millis();
    return delay(20);
  }
  if (lastRound && millis() - lastRound < PUSH_MS) return delay(20);
  lastRound = millis();

  for (int i = 0; i < count; i++) readPrinter(printers[i]);
  JsonDocument d;
  JsonArray items = d["items"].to<JsonArray>();
  long now = nowS();
  for (int i = 0; i < count; i++) {
    Printer &p = printers[i];
    feed::item(items.add<JsonObject>(), p.id.c_str(), p.model.c_str(), p.pr.as<JsonObjectConst>(), p.seen, p.since, now, p.fails < 2);
  }
  String body;
  serializeJson(d, body);
  bool ok = push(body);
  if (ok) lastOk = millis();

  // บอกเฉพาะตอนสรุปเปลี่ยน ไม่พ่นทุก 30 วิ
  int n[6] = {};
  const char *names[] = {"idle", "printing", "paused", "done", "error", "offline"};
  const char *thai[] = {"ว่าง", "กำลังปริ้น", "พัก", "เสร็จ", "ผิดพลาด", "ออฟไลน์"};
  for (JsonObjectConst x : items)
    for (int k = 0; k < 6; k++)
      if (!strcmp(x["state"], names[k])) n[k]++;
  String line = ok ? "ส่งแล้ว:" : "ส่งไม่ได้:";
  for (int k = 0; k < 6; k++)
    if (n[k]) line += String(" ") + thai[k] + " " + n[k];
  if (line != lastLine) say("%s (ไอพี ESP %s)", line.c_str(), WiFi.localIP().toString().c_str()), lastLine = line;
}
