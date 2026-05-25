"""Create HA automation via SSH + automations.yaml."""
import requests, json, paramiko, base64, time

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJjMjgyZTY3ZGFlZTE0MGNiYWEzZjY1ODBmYzllYzE0MyIsImlhdCI6MTc3NTY3ODAxOSwiZXhwIjoyMDkxMDM4MDE5fQ.uF2QcQ8vbsj-Ir1hQtyvJ8zRAnAaCJsAYK2j8Z9lmgE"
BASE = "https://homeassistant.groosman.nl"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("YOUR_HA_IP", username="root", password="YOUR_SSH_PASSWORD")

automation_yaml = """
- id: eplucon_convert_constant_to_timed
  alias: "[Eplucon] Convert Constant to Time limit"
  description: "Every 30 minutes, convert zones in Constant mode to Time limit with default duration"
  mode: single
  trigger:
    - platform: time_pattern
      minutes: "/30"
  condition: []
  action:
    - service: eplucon_thermostat.convert_constant_to_timed
      data: {}
"""

# Read existing automations.yaml
stdin, stdout, stderr = ssh.exec_command("docker exec homeassistant cat /config/automations.yaml 2>/dev/null || echo '[]'")
existing = stdout.read().decode()
print(f"Existing automations.yaml: {len(existing)} bytes")

if "eplucon_convert_constant_to_timed" in existing:
    print("Automation already in automations.yaml, skipping")
else:
    new_content = existing.rstrip() + "\n" + automation_yaml
    b64 = base64.b64encode(new_content.encode()).decode()
    stdin, stdout, stderr = ssh.exec_command(f"echo '{b64}' | base64 -d | docker exec -i homeassistant tee /config/automations.yaml > /dev/null; echo OK")
    result = stdout.read().decode().strip()
    print(f"Written: {result}")

    # Reload automations
    r = requests.post(f"{BASE}/api/services/automation/reload", headers=HEADERS, verify=False)
    print(f"Automation reload: {r.status_code}")

    time.sleep(3)

    # Verify
    r = requests.get(f"{BASE}/api/states", headers=HEADERS, verify=False)
    states = r.json()
    for s in states:
        if "eplucon" in s["entity_id"] and "automation" in s["entity_id"]:
            print(f"Found: {s['entity_id']} state={s['state']}")

ssh.close()
