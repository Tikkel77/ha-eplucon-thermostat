"""Upload HA integration files and write helper via base64 over SSH."""
import paramiko
import base64
import os

HOST = "YOUR_HA_IP"
USER = "root"
PASSWORD = "YOUR_SSH_PASSWORD"
HA_BASE = "/config/custom_components/eplucon_thermostat"
LOCAL_BASE = r"C:\Users\bart\.config\opencode\ha-eplucon-thermostat\custom_components\eplucon_thermostat"
REPO_ROOT = r"C:\Users\bart\.config\opencode\ha-eplucon-thermostat"

# Files to upload: (local_path, remote_path)
INTEGRATION_FILES = [
    "coordinator.py",
    "portal.py",
    "__init__.py",
    "config_flow.py",
    "climate.py",
    "const.py",
    "sensor.py",
    "binary_sensor.py",
    "services.yaml",
    "api/client.py",
    "api/models.py",
]

EXTRA_FILES = [
    # Write helper script goes to /config/
    (os.path.join(REPO_ROOT, "eplucon_write_helper.py"), "/config/eplucon_write_helper.py"),
]


def ssh_cmd(client, cmd, timeout=30):
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    return out, err


def upload_b64(client, local_path, remote_path):
    with open(local_path, "rb") as f:
        content = f.read()
    b64 = base64.b64encode(content).decode("ascii")
    chunk_size = 40000
    chunks = [b64[i:i+chunk_size] for i in range(0, len(b64), chunk_size)]
    ssh_cmd(client, f"printf '%s' '{chunks[0]}' > /tmp/upload.b64")
    for chunk in chunks[1:]:
        ssh_cmd(client, f"printf '%s' '{chunk}' >> /tmp/upload.b64")
    out, err = ssh_cmd(client, f"base64 -d /tmp/upload.b64 > {remote_path}; echo OK")
    result = out.strip()
    name = os.path.basename(local_path)
    print(f"  {name} -> {remote_path}: {result}")
    return result == "OK"


if __name__ == "__main__":
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, port=22, username=USER, password=PASSWORD, timeout=15)

    # Clear pycache
    print("Clearing pycache...")
    ssh_cmd(client, f"rm -rf {HA_BASE}/__pycache__ {HA_BASE}/api/__pycache__")

    # Upload integration files
    print("\nUploading integration files...")
    for f in INTEGRATION_FILES:
        local = os.path.join(LOCAL_BASE, f)
        remote = f"{HA_BASE}/{f}"
        upload_b64(client, local, remote)

    # Upload extra files
    print("\nUploading extra files...")
    for local, remote in EXTRA_FILES:
        upload_b64(client, local, remote)

    # Verify key files
    print("\nVerifying coordinator.py async_set_temperature...")
    out, _ = ssh_cmd(client, f"grep -n 'async_set_constant_temp\\|_run_write' {HA_BASE}/coordinator.py")
    print(out or "  (no _run_write references found - good)")

    print("Verifying write helper...")
    out, _ = ssh_cmd(client, "head -5 /config/eplucon_write_helper.py")
    print(out)

    print("File timestamps:")
    out, _ = ssh_cmd(client, f"ls -la {HA_BASE}/coordinator.py {HA_BASE}/portal.py /config/eplucon_write_helper.py")
    print(out)

    client.close()
    print("\nDone. Restart HA to apply changes.")
