// อ่านเคสจาก stdin {"cases":[{id, model, print, seen, since, now, connected}]} พิมพ์ {"items":[...]} ให้ test_feed.py เทียบกับ bambu.py
#include <iostream>
#include "../TeewangBambuESP/feed.h"

int main() {
  JsonDocument in, out;
  if (deserializeJson(in, std::cin)) return 2;
  JsonArray items = out["items"].to<JsonArray>();
  for (JsonObjectConst c : in["cases"].as<JsonArrayConst>())
    feed::item(items.add<JsonObject>(), c["id"], c["model"], c["print"], c["seen"], c["since"], c["now"], c["connected"]);
  serializeJson(out, std::cout);
}
