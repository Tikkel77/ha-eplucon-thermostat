"""Set up browser_mod integration on HA."""
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

# Start config flow for browser_mod (HA 2024+ WS command)
ws.send(json.dumps({
    "id": 1,
    "type": "config/config_entries/flow",
    "handler": "browser_mod",
    "show_advanced_options": False,
}))
msg = json.loads(ws.recv())
print("Flow start:", json.dumps(msg, indent=2))

# If it returned a form, we need to complete it
if msg.get("success") and msg.get("result", {}).get("type") == "form":
    flow_id = msg["result"]["flow_id"]
    print(f"Got form, flow_id={flow_id}, completing...")
    ws.send(json.dumps({
        "id": 2,
        "type": "config_entries/flow/" + flow_id,
        "handler": "browser_mod",
    }))
    msg = json.loads(ws.recv())
    print("Flow complete:", json.dumps(msg, indent=2))
elif msg.get("success") and msg.get("result", {}).get("type") == "create_entry":
    print("Entry created directly!")
elif msg.get("success") and msg.get("result", {}).get("type") == "abort":
    print(f"Aborted: {msg['result'].get('reason')}")

# Verify services now exist
ws.send(json.dumps({"id": 10, "type": "get_services"}))
msg = json.loads(ws.recv())
bm = msg.get("result", {}).get("browser_mod", {})
print(f"\nbrowser_mod services: {list(bm.keys()) if bm else 'STILL NOT FOUND'}")

ws.close()
