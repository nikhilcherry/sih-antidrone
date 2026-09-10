#!/usr/bin/env bash
# EO node bring-up. RUN THIS ON THE RASPBERRY PI, not the laptop.
#
# Puts the Pi's OTG port into USB-Ethernet gadget mode so a single USB cable to
# the laptop carries the video link with no router, no Wi-Fi and no DHCP.
# Pi becomes 10.55.0.1, laptop becomes 10.55.0.2.
#
#   ./scripts/setup_pi_gadget.sh              # configure the link
#   ./scripts/setup_pi_gadget.sh --service    # also autostart the EO sender
#
# Reboot required afterwards -- the overlay is applied by the bootloader.
set -euo pipefail

PI_IP=10.55.0.1
PREFIX=29           # 10.55.0.0/29 -- 6 usable addresses, plenty for a point link
INSTALL_SERVICE=0
[[ "${1:-}" == "--service" ]] && INSTALL_SERVICE=1

# Bookworm moved the boot partition. Support both so this works on an older card.
if [[ -d /boot/firmware ]]; then BOOT=/boot/firmware; else BOOT=/boot; fi
CONFIG="$BOOT/config.txt"
CMDLINE="$BOOT/cmdline.txt"

if [[ ! -f "$CONFIG" ]]; then
  echo "ERROR: $CONFIG not found. This script must run ON the Raspberry Pi." >&2
  exit 1
fi

MODEL=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)
echo "==> board: $MODEL"
case "$MODEL" in
  *"Pi 3"*|*"Pi 2"*|*"Pi Model B"*)
    echo "ERROR: this board has no OTG-capable port -- USB gadget mode is impossible." >&2
    echo "       Use Ethernet or Wi-Fi instead (see docs/eo_bringup.md)." >&2
    exit 1 ;;
  *"Pi Zero"*)
    echo "WARNING: Zero has ONE data USB port. Using it for gadget mode leaves" >&2
    echo "         nowhere to plug the webcam. Read docs/eo_bringup.md first." >&2
    read -r -p "         Continue anyway? [y/N] " ans
    [[ "$ans" == [yY] ]] || exit 1 ;;
esac

echo "==> backing up boot config"
sudo cp -n "$CONFIG" "$CONFIG.himkavach.bak"
sudo cp -n "$CMDLINE" "$CMDLINE.himkavach.bak"

echo "==> enabling the dwc2 OTG controller in $CONFIG"
if ! grep -q '^dtoverlay=dwc2' "$CONFIG"; then
  # Must land in [all], not under a filtered section, or it is silently ignored.
  printf '\n[all]\ndtoverlay=dwc2\n' | sudo tee -a "$CONFIG" >/dev/null
else
  echo "    already present"
fi

echo "==> loading dwc2 + g_ether at boot via $CMDLINE"
if ! grep -q 'modules-load=dwc2' "$CMDLINE"; then
  # cmdline.txt is ONE line. Anything on a second line is ignored, which is the
  # classic way this step appears to work and does nothing.
  sudo sed -i 's/\brootwait\b/rootwait modules-load=dwc2,g_ether/' "$CMDLINE"
  grep -q 'modules-load=dwc2' "$CMDLINE" || {
    echo "ERROR: no 'rootwait' token in $CMDLINE -- add modules-load=dwc2,g_ether by hand." >&2
    exit 1; }
else
  echo "    already present"
fi

echo "==> pinning $PI_IP on usb0"
# NetworkManager owns interfaces on Bookworm but will not know about usb0 until
# it exists, so a oneshot unit ordered after the device is the reliable path.
sudo tee /etc/systemd/system/himkavach-usb0.service >/dev/null <<UNIT
[Unit]
Description=HIMKAVACH EO link: static address on the USB gadget interface
After=sys-subsystem-net-devices-usb0.device
BindsTo=sys-subsystem-net-devices-usb0.device

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/sbin/ip addr replace $PI_IP/$PREFIX dev usb0
ExecStart=/sbin/ip link set usb0 up

[Install]
WantedBy=sys-subsystem-net-devices-usb0.device
UNIT
sudo systemctl enable himkavach-usb0.service

if [[ $INSTALL_SERVICE == 1 ]]; then
  echo "==> installing the EO sender as a service"
  REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
  sudo tee /etc/systemd/system/himkavach-eo.service >/dev/null <<UNIT
[Unit]
Description=HIMKAVACH EO sender (USB webcam -> laptop)
After=himkavach-usb0.service
Wants=himkavach-usb0.service

[Service]
User=$USER
WorkingDirectory=$REPO
ExecStart=/usr/bin/python3 -m himkavach.eo.sender --device /dev/video0 --size 1280x720 --fps 30
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
UNIT
  sudo systemctl enable himkavach-eo.service
fi

echo
echo "==> done. REBOOT the Pi, then plug the USB cable into the laptop:"
echo "      Pi Zero  -> the port marked USB (not PWR)"
echo "      Pi 4 / 5 -> the USB-C port"
echo
echo "    On the Pi after reboot:   ip addr show usb0        # expect $PI_IP"
echo "    On the laptop:            ./scripts/laptop_usb_link.sh"
echo
echo "    Undo:  sudo cp $CONFIG.himkavach.bak $CONFIG && sudo cp $CMDLINE.himkavach.bak $CMDLINE"
