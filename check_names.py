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

ws.send(json.dumps({"id": 1, "type": "get_states"}))
msg = json.loads(ws.recv())
while msg.get("id") != 1:
    msg = json.loads(ws.recv())

for s in msg["result"]:
    eid = s["entity_id"]
    if "lotte" in eid.lower():
        fn = s["attributes"].get("friendly_name", "N/A")
        print(f"{eid}: friendly_name={fn}")

ws.close()
