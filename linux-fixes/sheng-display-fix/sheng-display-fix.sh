#!/bin/sh
# Preserve Deepin stock display-setup-script behavior
if [ -x /var/lib/lastore/scripts/gen_upgrade_check_config.sh ]; then
    /var/lib/lastore/scripts/gen_upgrade_check_config.sh || true
fi

# Only perform the DSI-1 + backlight bring-up once per boot
if [ -e /run/sheng-display-fix.done ]; then
    exit 0
fi
touch /run/sheng-display-fix.done 2>/dev/null || true

LOG=/var/log/sheng-display-diag.log
exec >"$LOG" 2>&1
echo "=== sheng-display-fix (display-setup-script) start: $(date) ==="

export DISPLAY="${DISPLAY:-:0}"
if [ -z "$XAUTHORITY" ] && [ -e /var/run/lightdm/root/:0 ]; then
    export XAUTHORITY=/var/run/lightdm/root/:0
fi

echo "--- BEFORE ---"
for f in brightness actual_brightness bl_power max_brightness; do
    echo "bl/$f=$(cat /sys/class/backlight/ktz8866-backlight/$f 2>&1)"
done
for f in status enabled dpms; do
    echo "drm/$f=$(cat /sys/class/drm/card0-DSI-1/$f 2>&1)"
done
xrandr --query 2>&1 || true

echo "--- APPLY ---"
xset s off -dpms 2>&1 || true
xset dpms force on 2>&1 || true
xrandr --output DSI-1 --off 2>&1 || true
sleep 0.6
xrandr --output DSI-1 --mode 3048x2032 --rate 120 2>&1 || true
sleep 0.5

BL=/sys/class/backlight/ktz8866-backlight/brightness
SAVED=$(cat "$BL" 2>/dev/null || echo 1500)
case "$SAVED" in
    ''|*[!0-9]*) SAVED=1500 ;;
esac
if [ "$SAVED" -lt 400 ]; then
    SAVED=1500
fi
echo 0 > "$BL" 2>&1 || true
sleep 0.3
echo "$SAVED" > "$BL" 2>&1 || true
sleep 0.2

echo "--- XI2 TOUCH CHECK/ENABLE ---"
python3 - <<'PY' 2>&1 || true
import ctypes, os

libX11 = ctypes.CDLL("libX11.so.6")
libXi = ctypes.CDLL("libXi.so.6")

class XIDeviceInfo(ctypes.Structure):
    _fields_ = [
        ("deviceid", ctypes.c_int),
        ("name", ctypes.c_char_p),
        ("use", ctypes.c_int),
        ("attachment", ctypes.c_int),
        ("enabled", ctypes.c_int),
        ("num_classes", ctypes.c_int),
        ("classes", ctypes.c_void_p),
    ]

libX11.XOpenDisplay.restype = ctypes.c_void_p
libX11.XOpenDisplay.argtypes = [ctypes.c_char_p]
libX11.XInternAtom.restype = ctypes.c_ulong
libX11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
libX11.XFlush.argtypes = [ctypes.c_void_p]
libX11.XCloseDisplay.argtypes = [ctypes.c_void_p]

libXi.XIQueryDevice.restype = ctypes.POINTER(XIDeviceInfo)
libXi.XIQueryDevice.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
libXi.XIFreeDeviceInfo.argtypes = [ctypes.POINTER(XIDeviceInfo)]
libXi.XIChangeProperty.argtypes = [
    ctypes.c_void_p, ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
    ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int
]

dpy = libX11.XOpenDisplay(os.environ.get("DISPLAY", ":0").encode())
if not dpy:
    print("XOpenDisplay failed")
else:
    prop = libX11.XInternAtom(dpy, b"Device Enabled", 0)
    ndevices = ctypes.c_int(0)
    devs = libXi.XIQueryDevice(dpy, 0, ctypes.byref(ndevices))  # XIAllDevices = 0
    for i in range(ndevices.value):
        d = devs[i]
        name = d.name.decode("utf-8", "replace") if d.name else ""
        if "NVT" in name or "Touch" in name:
            print(f"dev id={d.deviceid} name={name!r} enabled={d.enabled}")
            if not d.enabled:
                val = (ctypes.c_ubyte * 1)(1)
                libXi.XIChangeProperty(dpy, d.deviceid, prop, 19, 8, 0, val, 1)  # XA_INTEGER=19, PropModeReplace=0
                print(f"  -> re-enabled dev id={d.deviceid}")
    libXi.XIFreeDeviceInfo(devs)
    libX11.XFlush(dpy)
    libX11.XCloseDisplay(dpy)
PY

echo "--- AFTER ---"
for f in brightness actual_brightness bl_power; do
    echo "bl/$f=$(cat /sys/class/backlight/ktz8866-backlight/$f 2>&1)"
done
for f in status enabled dpms; do
    echo "drm/$f=$(cat /sys/class/drm/card0-DSI-1/$f 2>&1)"
done
xrandr --query 2>&1 | head -n 10 || true
sync
exit 0
