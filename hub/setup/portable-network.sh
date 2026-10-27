#!/bin/bash
# Make the Pi work on any network, once. Run on the Pi:  sudo bash portable-network.sh
#  * firewall: accept every private (LAN) range instead of one subnet; the internet stays blocked
#  * mDNS: answer piscreen.local with IPv4 only (its global IPv6 address is firewalled, which made
#    PCs stall trying it first)
#  * NAS SSD: mount it on first use instead of only at boot, so a drive that shows up late (slow
#    USB start, plugged in after boot) still gets mounted; then mount it now and restart the apps
# The Pi already takes its address from the router (DHCP), so a new network just works after this.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || exec sudo bash "$0" "$@"

for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 fc00::/7 fe80::/10; do
  ufw allow from "$net" comment "LAN (pinest)" >/dev/null
done
echo "firewall: all private LAN ranges allowed"

sed -i 's/^use-ipv6=yes/use-ipv6=no/' /etc/avahi/avahi-daemon.conf
systemctl restart avahi-daemon
echo "mDNS: piscreen.local now answers with IPv4"

if grep -q " /srv/nas " /etc/fstab && ! grep " /srv/nas " /etc/fstab | grep -q "x-systemd.automount"; then
  cp -p /etc/fstab /etc/fstab.before-automount
  sed -i '/ \/srv\/nas /s/nofail/nofail,x-systemd.automount/' /etc/fstab
  systemctl daemon-reload
  echo "NAS: /srv/nas now mounts on first use (old fstab saved as /etc/fstab.before-automount)"
fi
mountpoint -q /srv/nas || mount /srv/nas
findmnt -n -o SOURCE,TARGET /srv/nas
systemctl restart pihub musik picloud
sleep 3
systemctl is-active pihub musik picloud | paste -sd' ' | sed 's/^/services: /'
echo "Done: the Pi is reachable as piscreen.local on any LAN it joins."
