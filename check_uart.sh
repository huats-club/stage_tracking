#!/bin/bash
# UART readiness check for Raspberry Pi -> BU03-Kit
# Run: bash check_uart.sh

# Color helpers
G='\033[0;32m'  # green = pass
Y='\033[0;33m'  # yellow = warn
R='\033[0;31m'  # red = fail
N='\033[0m'     # reset

pass() { echo -e "${G}[ OK ]${N} $1"; }
warn() { echo -e "${Y}[WARN]${N} $1"; }
fail() { echo -e "${R}[FAIL]${N} $1"; }
info() { echo -e "       $1"; }

echo "==================================================="
echo "  UART readiness check — Pi <-> BU03-Kit"
echo "==================================================="
echo

# 1. Is /dev/serial0 present?
echo "--- 1. Serial device ---"
if [ -e /dev/serial0 ]; then
    target=$(readlink -f /dev/serial0)
    pass "/dev/serial0 exists -> $target"
    case "$target" in
        *ttyAMA0) info "Pointing at PL011 (high-quality UART). Ideal." ;;
        *ttyS0)   warn "Pointing at mini-UART. Works at 115200 but less stable."
                  info "To switch to PL011: add 'dtoverlay=disable-bt' to /boot/firmware/config.txt and reboot." ;;
        *)        warn "Unknown target — check manually." ;;
    esac
else
    fail "/dev/serial0 not found."
    info "Run: sudo raspi-config -> Interface Options -> Serial Port"
    info "  Login shell over serial: NO"
    info "  Serial hardware enabled: YES"
fi
echo

# 2. Login shell on serial?
echo "--- 2. Serial console (login shell) ---"
cmdline=$(cat /boot/firmware/cmdline.txt 2>/dev/null || cat /boot/cmdline.txt 2>/dev/null)
if echo "$cmdline" | grep -qE 'console=(serial0|ttyAMA0|ttyS0)'; then
    fail "Serial login console is ENABLED in cmdline.txt — it will fight your script."
    info "Fix in raspi-config: Login shell over serial: NO"
else
    pass "No serial login shell on cmdline.txt"
fi
if systemctl is-enabled serial-getty@ttyAMA0.service &>/dev/null && \
   [ "$(systemctl is-enabled serial-getty@ttyAMA0.service 2>/dev/null)" = "enabled" ]; then
    fail "serial-getty@ttyAMA0 is enabled (login shell on UART)."
    info "Disable: sudo systemctl disable --now serial-getty@ttyAMA0.service"
else
    pass "serial-getty@ttyAMA0 not active"
fi
if systemctl is-enabled serial-getty@ttyS0.service &>/dev/null && \
   [ "$(systemctl is-enabled serial-getty@ttyS0.service 2>/dev/null)" = "enabled" ]; then
    fail "serial-getty@ttyS0 is enabled (login shell on UART)."
    info "Disable: sudo systemctl disable --now serial-getty@ttyS0.service"
else
    pass "serial-getty@ttyS0 not active"
fi
echo

# 3. UART enabled in config.txt?
echo "--- 3. config.txt UART settings ---"
cfg=""
if   [ -f /boot/firmware/config.txt ]; then cfg=/boot/firmware/config.txt
elif [ -f /boot/config.txt ];          then cfg=/boot/config.txt
fi
if [ -n "$cfg" ]; then
    info "Reading $cfg"
    if grep -qE '^\s*enable_uart\s*=\s*1' "$cfg"; then
        pass "enable_uart=1 set"
    else
        warn "enable_uart=1 not found explicitly (may still work via raspi-config)"
    fi
    if grep -qE '^\s*dtoverlay\s*=\s*disable-bt' "$cfg"; then
        pass "dtoverlay=disable-bt set (PL011 freed from Bluetooth)"
    else
        warn "dtoverlay=disable-bt not set — PL011 may be tied to Bluetooth on Pi 3/4/5."
        info "For best UART reliability, add this line to $cfg and reboot:"
        info "  dtoverlay=disable-bt"
    fi
else
    warn "Could not find config.txt"
fi
echo

# 4. dialout group membership (so we don't need sudo)
echo "--- 4. User permissions ---"
if id -nG "$USER" | tr ' ' '\n' | grep -qx dialout; then
    pass "User '$USER' is in 'dialout' group"
else
    warn "User '$USER' NOT in 'dialout' group — you'll need sudo to open the port."
    info "Fix: sudo usermod -aG dialout $USER  (then log out / back in)"
fi
echo

# 5. Is anything currently holding the port?
echo "--- 5. Port not held by another process ---"
if command -v lsof &>/dev/null; then
    holders=$(lsof /dev/serial0 2>/dev/null; lsof /dev/ttyAMA0 2>/dev/null; lsof /dev/ttyS0 2>/dev/null)
    if [ -z "$holders" ]; then
        pass "No process is holding /dev/serial0, /dev/ttyAMA0, or /dev/ttyS0"
    else
        fail "Something is holding the UART:"
        echo "$holders"
    fi
else
    warn "'lsof' not installed — skipping (sudo apt install lsof)"
fi
echo

# 6. pyserial available?
echo "--- 6. Python pyserial ---"
if python3 -c "import serial; print(serial.__version__)" &>/dev/null; then
    ver=$(python3 -c "import serial; print(serial.__version__)")
    pass "pyserial installed (version $ver)"
else
    fail "pyserial not installed."
    info "Fix: sudo apt install python3-serial   OR   pip install pyserial"
fi
echo

# 7. Quick read test (non-destructive)
echo "--- 7. Live read test (3 sec, no writes) ---"
if [ -e /dev/serial0 ] && [ -r /dev/serial0 ]; then
    info "Listening on /dev/serial0 @ 115200 for 3 seconds..."
    info "(If your BU03 is wired and powered, you may see junk or nothing — that's OK.)"
    timeout 3 python3 - <<'PY' 2>/dev/null || true
import serial, sys
try:
    s = serial.Serial("/dev/serial0", 115200, timeout=0.5)
    data = s.read(512)
    if data:
        print(f"       Got {len(data)} bytes: {data[:80]!r}")
    else:
        print("       No data received (normal if board idle).")
    s.close()
except Exception as e:
    print(f"       Could not open port: {e}")
PY
else
    warn "Skipped — /dev/serial0 not readable by current user"
fi
echo

echo "==================================================="
echo "  Done. Resolve any [FAIL] before running the script."
echo "  [WARN]s are usually fine but worth knowing."
echo "==================================================="
