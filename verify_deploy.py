"""Take screenshot of climate dashboard."""
import requests, json, urllib3, time
urllib3.disable_warnings()

BASE = "https://homeassistant.groosman.nl"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJjMjgyZTY3ZGFlZTE0MGNiYWEzZjY1ODBmYzllYzE0MyIsImlhdCI6MTc3NTY3ODAxOSwiZXhwIjoyMDkxMDM4MDE5fQ.uF2QcQ8vbsj-Ir1hQtyvJ8zRAnAaCJsAYK2j8Z9lmgE"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

# Check dashboard config is as expected
r = requests.get(f"{BASE}/api/lovelace/config/dashboard-climate", headers=HEADERS, verify=False)
if r.status_code == 200:
    config = r.json()
    view = config.get("views", [{}])[0]
    layout_card = view.get("cards", [{}])[0]
    cards = layout_card.get("cards", [])
    print(f"Dashboard loaded: {len(cards)} cards total")
    
    # Count card types
    headings = sum(1 for c in cards if c.get("type") == "markdown")
    zones = sum(1 for c in cards if c.get("type") == "custom:vertical-stack-in-card")
    print(f"  Headings: {headings}, Zone cards: {zones}")
    
    # Check layout config
    layout = layout_card.get("layout", {})
    print(f"  Grid columns: {layout.get('grid-template-columns')}")
    print(f"  Media query: {layout.get('mediaquery')}")
    
    # Check first zone card structure
    first_zone = next((c for c in cards if c.get("type") == "custom:vertical-stack-in-card"), None)
    if first_zone:
        inner = first_zone.get("cards", [])
        print(f"  First zone card has {len(inner)} inner cards")
        # Check +/- buttons
        controls = inner[1] if len(inner) > 1 else {}
        if controls.get("type") == "horizontal-stack":
            minus = controls.get("cards", [{}])[0]
            print(f"  Minus button border-radius: {[s for s in minus.get('styles', {}).get('card', []) if 'border-radius' in s]}")
            print(f"  Minus button card_mod: {'card_mod' in minus}")
            plus = controls.get("cards", [{}])[2] if len(controls.get("cards", [])) > 2 else {}
            print(f"  Plus button card_mod: {'card_mod' in plus}")
        
        # Check schedule button in mode_row
        if len(inner) > 2:
            mode_row = inner[2]
            if mode_row.get("type") == "horizontal-stack":
                sched = mode_row.get("cards", [{}])[-1]
                tap = sched.get("tap_action", {})
                print(f"  Schedule btn tap_action: {tap.get('action')}")
                bm = tap.get("browser_mod", {})
                print(f"  browser_mod service: {bm.get('service')}")
                content = bm.get("data", {}).get("content", {})
                print(f"  popup content type: {content.get('type')}")
                popup_cards = content.get("cards", [])
                print(f"  popup has {len(popup_cards)} cards")
else:
    print(f"Failed to load dashboard: {r.status_code}")
