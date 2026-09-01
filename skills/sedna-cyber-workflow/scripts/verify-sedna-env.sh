#!/usr/bin/env bash
set -euo pipefail

failures=0

echo "=== [1/4] Checking Host HTB/Lab VPN ==="
TUN_IFACE=""
if IP_OUTPUT="$(ip -brief addr 2>/dev/null)"; then
    while read -r iface state _rest; do
        if [[ "${iface}" =~ ^tun[0-9]+$ && "${state}" == "UP" ]]; then
            TUN_IFACE="${iface}"
            break
        fi
    done <<<"${IP_OUTPUT}"
fi
if [[ -n "${TUN_IFACE}" ]]; then
    echo "[+] Found active VPN interface: ${TUN_IFACE}"
else
    echo "[!] ERROR: No UP tun VPN interface detected on host." >&2
    failures=$((failures + 1))
fi

echo "=== [2/4] Checking HexStrike Kali Container and API ==="
HEXSTRIKE_RUNNING=0
if DOCKER_NAMES="$(docker ps --format '{{.Names}}' 2>/dev/null)"; then
    while IFS= read -r container_name; do
        if [[ "${container_name}" == "hexstrike-kali" ]]; then
            HEXSTRIKE_RUNNING=1
            break
        fi
    done <<<"${DOCKER_NAMES}"
fi
if ((HEXSTRIKE_RUNNING == 1)); then
    echo "[+] Container hexstrike-kali is running."
    HEALTH_MAX_BYTES=4096
    if HEALTH_RESPONSE="$(
        curl -fsS \
            --connect-timeout 2 \
            --max-time 5 \
            --max-filesize "${HEALTH_MAX_BYTES}" \
            http://127.0.0.1:8888/health 2>/dev/null
    )" &&
        ((${#HEALTH_RESPONSE} <= HEALTH_MAX_BYTES)) &&
        python3 -c '
import json
import sys


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


try:
    payload = json.loads(sys.argv[1], object_pairs_hook=unique_object)
except (TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
raise SystemExit(0 if payload == {"status": "healthy"} else 1)
' "${HEALTH_RESPONSE}"; then
        echo "[+] HexStrike API is healthy on 127.0.0.1:8888."
    else
        echo "[!] ERROR: HexStrike API did not return status=healthy." >&2
        failures=$((failures + 1))
    fi
else
    echo "[!] ERROR: Container hexstrike-kali is not running." >&2
    failures=$((failures + 1))
fi

echo "=== [3/4] Checking pt-report Script ==="
if [[ -f "./pt-report.py" ]] || command -v pt-report.py >/dev/null 2>&1; then
    echo "[+] pt-report.py is available."
else
    echo "[!] ERROR: pt-report.py not found in current directory or PATH." >&2
    failures=$((failures + 1))
fi

if ((failures > 0)); then
    echo "[!] Environment verification failed with ${failures} blocking error(s)." >&2
    exit 1
fi

echo "=== [4/4] Initializing Sedna Knowledge Root ==="
umask 077
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SEDNA_ROOT="${HERMES_HOME}/knowledge/sedna"
echo "[*] Default Sedna Root: ${SEDNA_ROOT}"
mkdir -p "${SEDNA_ROOT}/semantic_bundles" "${SEDNA_ROOT}/indexes" "${SEDNA_ROOT}/engagements"
echo "[+] Sedna directories initialized."
echo "=== Verification Finished ==="
