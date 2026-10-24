// แปลงรายงานของเครื่อง Bambu เป็นสถานะที่ POST /api/feed รับ ตรงกับ item() + detail() ใน bambu.py ทุกข้อ
// ใช้แค่ ArduinoJson กับ std ไม่พึ่ง Arduino เลยคอมไพล์เทสบน Mac ได้ (test_feed.py เทียบกับ bambu.py)
#pragma once
#include <ArduinoJson.h>
#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <string>

namespace feed {

const long OFFLINE_S = 360;
const long DONE_AS_IDLE_S = 30 * 60;  // เครื่องค้าง FINISH/FAILED จนเริ่มงานใหม่ เกินนี้นับว่าว่าง (เหมือน bambu.py)

// ตัวเลขที่เครื่องส่งมาเป็นทศนิยมหรือข้อความก็ได้ ปัดแบบ round() ของ Python คืน false ถ้าไม่ใช่ตัวเลขหรือนอกช่วง
inline bool num(JsonVariantConst v, long lo, long hi, long &out) {
  double d;
  if (v.is<bool>()) d = v.as<bool>();
  else if (v.is<double>()) d = v.as<double>();
  else if (v.is<const char *>()) {
    const char *s = v.as<const char *>();
    char *end;
    d = strtod(s, &end);
    while (*end == ' ' || *end == '\t' || *end == '\n' || *end == '\r') end++;
    if (end == s || *end) return false;
  } else return false;
  if (!isfinite(d)) return false;
  d = nearbyint(d);
  if (d < lo || d > hi) return false;
  out = (long)d;
  return true;
}

// เลขช่อง AMS: "0" หรือ 0 ได้ ค่าอื่นคืน -1
inline long idx(JsonVariantConst v) {
  if (v.is<long long>()) return v.as<long long>() >= 0 ? (long)v.as<long long>() : -1;
  if (!v.is<const char *>()) return -1;
  const char *s = v.as<const char *>();
  if (!*s) return -1;
  for (const char *c = s; *c; c++)
    if (*c < '0' || *c > '9') return -1;
  return strtol(s, nullptr, 10);
}

inline std::string hexcode(unsigned long long n, int parts) {
  std::string s;
  char b[6];
  for (int i = parts - 1; i >= 0; i--) {
    snprintf(b, sizeof b, "%04X", (unsigned)((n >> (16 * i)) & 0xFFFF));
    if (!s.empty()) s += '-';
    s += b;
  }
  return s;
}

inline std::string trim(const char *s) {
  std::string t(s);
  const char *ws = " \t\n\r\f\v";
  size_t a = t.find_first_not_of(ws);
  if (a == std::string::npos) return "";
  return t.substr(a, t.find_last_not_of(ws) - a + 1);
}

// ตัดเหลือ n ตัวอักษร (ไม่ใช่ n ไบต์) ไม่ตัดกลางตัวไทย
inline std::string cut(const std::string &s, size_t n) {
  size_t chars = 0;
  for (size_t i = 0; i < s.size(); i++)
    if (((unsigned char)s[i] & 0xC0) != 0x80 && ++chars > n) return s.substr(0, i);
  return s;
}

inline bool tray(JsonArray out, const std::string &slot, JsonObjectConst t) {
  std::string ty;
  if (t["tray_type"].is<const char *>())
    for (const char *c = t["tray_type"]; *c; c++)
      if (isalnum((unsigned char)*c) || strchr("+-_ .", *c)) ty += *c;
  ty = cut(trim(ty.c_str()), 16);
  if (ty.empty()) return false;
  JsonObject x = out.add<JsonObject>();
  x["slot"] = slot;
  x["type"] = ty;
  const char *col = t["tray_color"].is<const char *>() ? t["tray_color"].as<const char *>() : "";
  std::string c(col, strnlen(col, 6));
  bool hex = c.size() == 6;
  for (char &ch : c) {
    ch = toupper((unsigned char)ch);
    hex = hex && isxdigit((unsigned char)ch);
  }
  if (hex) x["color"] = c;
  else x["color"] = nullptr;
  JsonVariantConst r = t["remain"];
  if (r.is<long long>() && r.as<long long>() >= 0 && r.as<long long>() <= 100) x["remain"] = r.as<long long>();
  else x["remain"] = nullptr;
  return true;
}

// p = ช่อง "print" ของรายงานเต็มล่าสุด
inline void detail(JsonObject out, JsonObjectConst p) {
  static const struct { const char *src, *dst; long lo, hi; } NUMS[] = {
      {"nozzle_temper", "nozzle", 0, 500}, {"bed_temper", "bed", 0, 200}, {"chamber_temper", "chamber", 0, 100},
      {"layer_num", "layer", 0, 100000}, {"total_layer_num", "layers", 0, 100000}};
  for (auto &k : NUMS) {
    long v;
    if (num(p[k.src], k.lo, k.hi, v)) out[k.dst] = v;
  }
  if (p["subtask_name"].is<const char *>()) {
    std::string job = trim(p["subtask_name"]);
    if (!job.empty()) out["job"] = cut(job, 80);
  }
  JsonArray trays = out["trays"].to<JsonArray>();
  for (JsonVariantConst unit : p["ams"]["ams"].as<JsonArrayConst>()) {
    long u = idx(unit["id"]);
    if (!unit.is<JsonObjectConst>() || u < 0 || u > 3) continue;
    for (JsonVariantConst t : unit["tray"].as<JsonArrayConst>()) {
      long i = idx(t["id"]);
      if (t.is<JsonObjectConst>() && i >= 0 && i <= 3) tray(trays, std::string(1, "ABCD"[u]) + char('1' + i), t);
    }
  }
  if (p["vt_tray"].is<JsonObjectConst>()) tray(trays, "EXT", p["vt_tray"]);
  if (trays.size() == 0) out.remove("trays");
  // tray_now: 0-15 = AMS ตัวที่ (n / 4) ช่อง (n % 4), 254 = ช่องนอก, 255 = ไม่ได้ใช้
  long now = idx(p["ams"]["tray_now"]);
  if (now >= 0 && now < 16) out["tray_now"] = std::string(1, "ABCD"[now / 4]) + char('1' + now % 4);
  else if (now == 254) out["tray_now"] = "EXT";
  JsonArray errors = out["errors"].to<JsonArray>();
  if (p["print_error"].is<long long>() && p["print_error"].as<long long>() > 0) errors.add(hexcode(p["print_error"].as<long long>(), 2));
  for (JsonVariantConst h : p["hms"].as<JsonArrayConst>())
    if (errors.size() < 8 && h["attr"].is<long long>() && h["code"].is<long long>())
      errors.add(hexcode(((unsigned long long)h["attr"].as<long long>() << 32) | (unsigned long long)h["code"].as<long long>(), 4));
  if (errors.size() == 0) out.remove("errors");
}

// เวลาเป็นวินาทีนับจากเปิดเครื่อง seen = ได้รายงานล่าสุดเมื่อไร since = gcode_state เปลี่ยนล่าสุดเมื่อไร
inline void item(JsonObject out, const char *id, const char *model, JsonObjectConst p, long seen, long since, long now, bool connected) {
  out["id"] = id;
  if (model && *model) out["model"] = model;
  const char *g = p["gcode_state"];
  if (!connected || now - seen > OFFLINE_S || !g) {
    out["state"] = "offline";
    return;
  }
  static const char *STATE[][2] = {{"IDLE", "idle"}, {"PREPARE", "printing"}, {"SLICING", "printing"}, {"RUNNING", "printing"},
                                   {"PAUSE", "paused"}, {"FINISH", "done"}, {"FAILED", "error"}};
  const char *state = "idle";
  for (auto &s : STATE)
    if (!strcmp(g, s[0])) state = s[1];
  bool ended = !strcmp(state, "done") || !strcmp(state, "error");
  if (ended && now - since > DONE_AS_IDLE_S) state = "idle";
  out["state"] = state;
  detail(out, p);
  if (!strcmp(state, "printing") || !strcmp(state, "paused")) {
    JsonVariantConst left = p["mc_remaining_time"], pct = p["mc_percent"];
    if (left.is<long long>() && left.as<long long>() >= 0) out["left_min"] = left.as<long long>() < 10080 ? left.as<long long>() : 10080;
    if (pct.is<long long>() && pct.as<long long>() >= 0 && pct.as<long long>() <= 100) out["pct"] = pct.as<long long>();
  }
}

}  // namespace feed
