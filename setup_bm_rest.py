"""Try REST API to set up browser_mod."""
import requests, json, urllib3
urllib3.disable_warnings()

BASE = "https://homeassistant.groosman.nl"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJjMjgyZTY3ZGFlZTE0MGNiYWEzZjY1ODBmYzllYzE0MyIsImlhdCI6MTc3NTY3ODAxOSwiZXhwIjoyMDkxMDM4MDE5fQ.uF2QcQ8vbsj-Ir1hQtyvJ8zRAnAaCJsAYK2j8Z9lmgE"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Start config flow
r = requests.post(
    f"{BASE}/api/config/config_entries/flow",
    headers=HEADERS,
    json={"handler": "browser_mod", "show_advanced_options": False},
    verify=False,
)
print(f"Flow init: {r.status_code}")
print(json.dumps(r.json(), indent=2))

if r.status_code == 200:
    data = r.json()
    if data.get("type") == "form":
        flow_id = data["flow_id"]
        # Submit empty form
        r2 = requests.post(
            f"{BASE}/api/config/config_entries/flow/{flow_id}",
            headers=HEADERS,
            json={},
            verify=False,
        )
        print(f"\nFlow submit: {r2.status_code}")
        print(json.dumps(r2.json(), indent=2))
    elif data.get("type") == "create_entry":
        print("Entry created!")
