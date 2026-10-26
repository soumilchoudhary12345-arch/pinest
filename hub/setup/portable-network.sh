#!/bin/bash
# Make the Pi work on any home network, once. Run on the Pi:  sudo bash portable-network.sh
#  * firewall: accept every private (LAN) range instead of one subnet; the internet stays blocked
#  * mDNS: answer piscreen.local with IPv4 only (its global IPv6 address is firewalled, which made
#    PCs stall trying it first)
# The Pi already takes its address from the router (DHCP), so a new network just works after this.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || exec sudo bash "$0" "$@"
for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 fc00::/7 fe80::/10; do
  ufw allow from "$net" comment "LAN (pinest)" >/dev/null
done
sed -i 's/^use-ipv6=yes/use-ipv6=no/' /etc/avahi/avahi-daemon.conf
systemctl restart avahi-daemon
ufw status | sed -n '1,30p'
echo "Done: the Pi is reachable as piscreen.local on any LAN it joins."
