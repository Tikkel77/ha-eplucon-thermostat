"""Check browser_mod config entry status."""
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

# Check config entries for browser_mod
ws.send(json.dumps({"id": 1, "type": "config_entries/get"}))
msg = json.loads(ws.recv())
found = False
for e in msg.get("result", []):
    if "browser" in e.get("domain", "").lower():
        print(f"Config entry: domain={e['domain']} title={e['title']} state={e['state']}")
        found = True
if not found:
    print("No browser_mod config entry found - integration not set up!")

# List all domains that have services
ws.send(json.dumps({"id": 2, "type": "get_services"}))
msg = json.loads(ws.recv())
domains = sorted(msg.get("result", {}).keys())
print(f"\nAll service domains ({len(domains)}):")
for d in domains:
    if "browser" in d.lower() or "popup" in d.lower():
        print(f"  * {d}: {list(msg['result'][d].keys())}")

ws.close()
