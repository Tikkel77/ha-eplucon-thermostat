"""Standalone write helper for Eplucon portal — uses curl to bypass Python HTTP issues.

This script is deployed to /config/eplucon_write_helper.py on the HA container.
It runs as a subprocess from portal.py, receiving JSON args on stdin.

Uses curl for all HTTP requests to avoid any Python requests/urllib3/ssl issues
that may be inherited from the HA process context.
"""
import sys
import os
import json
import re
import subprocess
import tempfile
import time


LOG_FILE = "/config/write_helper_debug.log"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def log_debug(msg):
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def curl(
    url,
    cookie_jar,
    *,
    method="GET",
    data=None,
    headers=None,
    follow_redirects=True,
    timeout=40,
):
    """Run curl and return (status_code, final_url, body, meta)."""
    # Extended -w format to capture remote_ip, local_ip, http_version, redirect_url
    write_out = (
        "\n__CURL_META__\n"
        "http_code=%{http_code}\n"
        "url_effective=%{url_effective}\n"
        "remote_ip=%{remote_ip}\n"
        "remote_port=%{remote_port}\n"
        "local_ip=%{local_ip}\n"
        "local_port=%{local_port}\n"
        "http_version=%{http_version}\n"
        "redirect_url=%{redirect_url}\n"
    )
    cmd = [
        "curl", "-s",           # silent
        "-S",                    # show errors
        "-v",                    # verbose for debugging
        "--ipv4",                # force IPv4 to avoid LB variance
        "--http1.1",             # force HTTP/1.1
        "-w", write_out,
        "-b", cookie_jar,        # read cookies
        "-c", cookie_jar,        # write cookies
        "-A", USER_AGENT,
        "--max-time", str(timeout),
    ]
    if follow_redirects:
        cmd += ["-L", "--max-redirs", "10"]
    if method == "POST":
        cmd += ["-X", "POST"]
    if data:
        for k, v in data.items():
            cmd += ["--data-urlencode", f"{k}={v}"]
    if headers:
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
    cmd.append(url)

    log_debug(f"curl {method} {url}")
    result = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
    raw = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    if stderr:
        # Log relevant headers from verbose output
        for line in stderr.split("\n"):
            line_l = line.strip().lower()
            if any(h in line_l for h in ("set-cookie", "location:", "< http/", "connected to")):
                log_debug(f"  curl-hdr: {line.strip()}")
    if result.returncode != 0:
        raise RuntimeError(f"curl failed (rc={result.returncode}): {stderr}")

    # Parse output: body + __CURL_META__ + key=value lines
    meta = {}
    if "__CURL_META__" in raw:
        parts = raw.split("__CURL_META__", 1)
        body = parts[0].rstrip("\n")
        for mline in parts[1].strip().split("\n"):
            if "=" in mline:
                k, v = mline.split("=", 1)
                meta[k.strip()] = v.strip()
    else:
        body = raw
    
    status_code = int(meta.get("http_code", "0"))
    final_url = meta.get("url_effective", "")
    
    log_debug(
        f"  status={status_code} final_url={final_url} body_len={len(body)} "
        f"remote_ip={meta.get('remote_ip', '?')}:{meta.get('remote_port', '?')} "
        f"local_ip={meta.get('local_ip', '?')}:{meta.get('local_port', '?')} "
        f"http_ver={meta.get('http_version', '?')} "
        f"redirect_url={meta.get('redirect_url', '')}"
    )
    return status_code, final_url, body, meta


def extract_input_value(html, input_name):
    m = re.search(rf'name="{re.escape(input_name)}"[^>]*value="([^"]*)"', html)
    return m.group(1) if m else ""


def extract_honeypot_name(html):
    """Find honeypot field: an <input> with type="text" that isn't 'username'.

    The HTML may have name before type, so we match both orderings.
    """
    # Match inputs with type="text" — name could be before or after type
    pattern1 = r'<input[^>]*type="text"[^>]*name="([^"]+)"'
    pattern2 = r'<input[^>]*name="([^"]+)"[^>]*type="text"'
    names = set()
    names.update(re.findall(pattern1, html))
    names.update(re.findall(pattern2, html))
    for name in names:
        if name not in ("username", "valid_from"):
            return name
    return ""


def do_write(args):
    base = args["base_url"].rstrip("/")
    username = args["username"]
    password = args["password"]
    timeout = args.get("timeout", 40)
    ami = str(args["account_module_index"])
    mode = args["mode"]
    mode_id = args["mode_id"]
    zone_internal_id = args["zone_internal_id"]
    constant_temp_deci = args["constant_temp_deci"]
    active_schedule = args["active_schedule"]
    hours = args.get("hours")
    minutes = args.get("minutes")

    log_debug(f"=== NEW WRITE (curl) PID={os.getpid()} PPID={os.getppid()} ===")

    # Create temp cookie jar
    cookie_fd, cookie_jar = tempfile.mkstemp(prefix="eplucon_cookies_", suffix=".txt")
    os.close(cookie_fd)

    try:
        _do_write_with_cookies(
            base, username, password, timeout,
            ami, mode, mode_id, zone_internal_id,
            constant_temp_deci, active_schedule,
            hours, minutes, cookie_jar,
        )
    finally:
        try:
            os.unlink(cookie_jar)
        except OSError:
            pass


def _do_write_with_cookies(
    base, username, password, timeout,
    ami, mode, mode_id, zone_internal_id,
    constant_temp_deci, active_schedule,
    hours, minutes, cookie_jar,
):
    url_set = f"{base}/e-control/set_constant_temp?account_module_index={ami}"
    referer = f"{base}/e-control/zones?account_module_index={ami}"

    for attempt in range(2):
        # Fresh cookie jar on each attempt
        try:
            os.unlink(cookie_jar)
        except OSError:
            pass
        open(cookie_jar, "w").close()

        # Step 1: GET /login to obtain _token, valid_from, honeypot
        status, final_url, body, meta = curl(f"{base}/login", cookie_jar, timeout=timeout)
        login_remote_ip = meta.get("remote_ip", "unknown")
        log_debug(f"  GET /login remote_ip={login_remote_ip}")
        if status != 200:
            raise RuntimeError(f"GET /login returned {status}")

        token = extract_input_value(body, "_token")
        valid_from = extract_input_value(body, "valid_from")
        if not token:
            raise RuntimeError("_token not found on login page")

        honeypot = extract_honeypot_name(body)
        log_debug(f"  _token={token[:20]}... valid_from={valid_from[:40]}... honeypot={honeypot!r}")

        # Step 2: POST /login
        login_data = {
            "_token": token,
            "username": username,
            "password": password,
            "remember": "1",
            "valid_from": valid_from,
        }
        if honeypot:
            login_data[honeypot] = ""

        # Log the exact POST data for comparison (mask password)
        debug_data = dict(login_data)
        debug_data["password"] = "***"
        log_debug(f"  login POST data keys: {list(login_data.keys())}")
        log_debug(f"  login POST _token length: {len(token)}")
        log_debug(f"  login POST valid_from length: {len(valid_from)}")

        status, final_url, body, meta = curl(
            f"{base}/login", cookie_jar,
            method="POST",
            data=login_data,
            headers={"Origin": base, "Referer": f"{base}/login"},
            follow_redirects=False,
            timeout=timeout,
        )
        redirect_url = meta.get("redirect_url", "")
        post_remote_ip = meta.get("remote_ip", "unknown")
        log_debug(f"  login POST status={status} redirect_url={redirect_url} remote_ip={post_remote_ip}")

        # Login POST returns 302 on success (redirect to /e-control)
        # and 200 or 419 on failure (re-displays login page)
        if status in (302, 301):
            if "/login" in redirect_url and "/e-control" not in redirect_url:
                # 302 back to /login = login REJECTED
                log_debug(f"  login REJECTED (302 back to /login). Body preview: {body[:500]!r}")
                if attempt == 0:
                    continue
                raise RuntimeError(f"Login rejected: 302 to {redirect_url}")
            log_debug("  login OK (redirect to e-control)")
        elif status < 400:
            # 200 means login page re-displayed = bad credentials
            log_debug(f"  login might have failed: status={status} body[:500]={body[:500]!r}")
            if attempt == 0:
                continue
            raise RuntimeError(f"Login returned {status} (expected 302)")
        else:
            log_debug(f"  login FAILED: status={status} body[:500]={body[:500]!r}")
            if attempt == 0:
                continue
            raise RuntimeError(f"Login failed: status={status}")

        # Dump cookie jar for debugging
        try:
            with open(cookie_jar, "r") as cf:
                jar_contents = cf.read()
            log_debug(f"  cookie jar after login:\n{jar_contents}"[:2000])
        except Exception as e:
            log_debug(f"  could not read cookie jar: {e}")

        # Step 3: GET zones page for CSRF token
        status, final_url, body, meta = curl(referer, cookie_jar, timeout=timeout)
        if status != 200:
            raise RuntimeError(f"GET zones returned {status} (may have redirected to login)")

        m = re.search(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', body)
        if not m:
            log_debug(f"  CSRF not found. Body preview: {body[:500]!r}")
            raise RuntimeError("CSRF token not found on zones page")
        csrf = m.group(1)
        log_debug(f"  CSRF={csrf[:20]}...")

        # Step 4: POST set_constant_temp
        post_data = {
            "_token": csrf,
            "mode": mode,
            "mode_id": str(mode_id),
            "zone_id": str(zone_internal_id),
            "parent_id": str(zone_internal_id),
            "constant_temp": str(constant_temp_deci),
            "active_schedule": str(active_schedule),
        }
        if hours is not None:
            post_data["hours"] = str(hours)
        if minutes is not None:
            post_data["minutes"] = str(minutes)

        status, final_url, body, meta = curl(
            url_set, cookie_jar,
            method="POST",
            data=post_data,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "X-CSRF-TOKEN": csrf,
                "Origin": base,
                "Referer": referer,
            },
            follow_redirects=False,
            timeout=timeout,
        )

        log_debug(f"  write status={status} body={body.strip()[:300]!r}")

        if status in (401, 419) and attempt == 0:
            log_debug("  -> auth failed, retrying with fresh session")
            continue

        if status >= 400:
            raise RuntimeError(f"HTTP_{status}: {body[:300]}")

        # Success: portal returns "1" or "" (empty body)
        return

    raise RuntimeError("Write failed after 2 attempts")


def main():
    try:
        args = json.loads(sys.stdin.read())
        do_write(args)
        print("OK")
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
