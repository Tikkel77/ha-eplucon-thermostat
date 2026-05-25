import asyncio, json, ssl
import websockets

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJjMjgyZTY3ZGFlZTE0MGNiYWEzZjY1ODBmYzllYzE0MyIsImlhdCI6MTc3NTY3ODAxOSwiZXhwIjoyMDkxMDM4MDE5fQ.uF2QcQ8vbsj-Ir1hQtyvJ8zRAnAaCJsAYK2j8Z9lmgE"
URI = "wss://homeassistant.groosman.nl/api/websocket"

async def main():
    ssl_ctx = ssl.create_default_context()
    async with websockets.connect(URI, ssl=ssl_ctx) as ws:
        msg = json.loads(await ws.recv())
        await ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
        msg = json.loads(await ws.recv())
        if msg["type"] != "auth_ok":
            print(f"Auth failed: {msg}")
            return

        # Host info (disk)
        await ws.send(json.dumps({"id": 1, "type": "supervisor/api", "endpoint": "/host/info", "method": "get"}))
        msg = json.loads(await ws.recv())
        data = msg.get("result", {}).get("data", msg.get("result", {}))
        print("=== HOST INFO ===")
        for k in ["disk_total", "disk_used", "disk_free", "disk_life_time", "hostname", "operating_system", "deployment"]:
            if k in data:
                print(f"  {k}: {data[k]}")

        # All addons
        await ws.send(json.dumps({"id": 2, "type": "supervisor/api", "endpoint": "/addons", "method": "get"}))
        msg = json.loads(await ws.recv())
        addons = msg.get("result", {}).get("data", {}).get("addons", [])
        print("\n=== SSH/TERMINAL ADDONS ===")
        for a in addons:
            name_lower = (a.get("slug", "") + a.get("name", "")).lower()
            if "ssh" in name_lower or "terminal" in name_lower:
                print(f"  {a['slug']}: {a.get('name','')} state={a.get('state','?')}")

        # Try to restart SSH addon
        print("\n=== RESTARTING SSH ADDON ===")
        await ws.send(json.dumps({"id": 3, "type": "supervisor/api", "endpoint": "/addons/a0d7b954_ssh/restart", "method": "post"}))
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
        print(f"Restart result: {json.dumps(msg, indent=2)}")

asyncio.run(main())
