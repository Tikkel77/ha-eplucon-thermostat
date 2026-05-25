"""Check browser_mod setup on HA."""
import json, ssl, websocket

ws = websocket.create_connection(
    "wss://homeassistant.groosman.nl/api/websocket",
    sslopt={"cert_reqs": ssl.CERT_NONE},
)
msg = json.loads(ws.recv())
ws.send(json.dumps({
    "type": "auth",
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJjMjgyZTY3ZGFlZTE0MGNiYWEzZjY1ODBmYzllYzE0MyIsImlhdCI6MTc3NTY3ODAxOSwiZXhwIjoyMDkxMDM4MDE5fQ.uF2QcQ8vbsj-Ir1hQtyvJ8zRAnAaCJsAYK2j8Z9lmgE",
}))
msg = json.loads(ws.recv())
print("Auth:", msg["type"])

# Check services
ws.send(json.dumps({"id": 1, "type": "get_services"}))
msg = json.loads(ws.recv())
bm = msg.get("result", {}).get("browser_mod", {})
print("browser_mod services:", list(bm.keys()) if bm else "NOT FOUND")

# Check lovelace resources
ws.send(json.dumps({"id": 2, "type": "lovelace/resources"}))
msg = json.loads(ws.recv())
for r in msg.get("result", []):
    url = r.get("url", "")
    if "button-card" in url or "browser_mod" in url or "layout-card" in url:
        print(f"Resource: {url}")

# Check HACS repos for browser_mod version
ws.send(json.dumps({"id": 3, "type": "hacs/repositories/list"}))
msg = json.loads(ws.recv())
for r in msg.get("result", []):
    name = r.get("name", "").lower()
    full = r.get("full_name", "").lower()
    if "browser" in name or "browser_mod" in full or "button-card" in name or "button-card" in full:
        print(f"HACS: {r.get('name')} v{r.get('installed_version')} (avail: {r.get('available_version')}) full={r.get('full_name')}")

ws.close()
