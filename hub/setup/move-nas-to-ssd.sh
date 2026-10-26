#!/usr/bin/env bash
# Wipe the EVM25 SSD, format ext4, move /srv/nas onto it, mount it permanently.
set -euo pipefail
DISK=/dev/disk/by-id/ata-EVM25_256GB_EVSSDNC256G032000005
DEV=$(readlink -f "$DISK")

# Safety: refuse unless it's exactly the disk we inspected and not the boot disk.
[ "$(lsblk -dno MODEL "$DEV" | xargs)" = "EVM25/256GB" ] || { echo "model mismatch, abort"; exit 1; }
[ "$(lsblk -dbno SIZE "$DEV")" -gt 200000000000 ] || { echo "size mismatch, abort"; exit 1; }
findmnt -rno SOURCE / | grep -q "^$DEV" && { echo "that's the root disk, abort"; exit 1; }

echo "== stopping services"
systemctl stop musik picloud pihub syncthing@som

echo "== unmounting $DEV partitions"
for p in $(lsblk -lnpo NAME "$DEV" | tail -n +2); do umount "$p" 2>/dev/null || true; done

echo "== wiping and partitioning"
wipefs -a "$DEV" >/dev/null
for p in $(lsblk -lnpo NAME "$DEV" | tail -n +2); do wipefs -a "$p" >/dev/null 2>&1 || true; done
parted -s "$DEV" mklabel gpt mkpart nas ext4 1MiB 100%
udevadm settle
PART=$(lsblk -lnpo NAME "$DEV" | tail -n +2 | head -1)
mkfs.ext4 -q -F -L nas -m 1 "$PART"
UUID=$(blkid -s UUID -o value "$PART")
echo "new partition $PART UUID=$UUID"

echo "== copying /srv/nas to the SSD"
mkdir -p /mnt/newnas
mount "$PART" /mnt/newnas
rsync -aHAX /srv/nas/ /mnt/newnas/
umount /mnt/newnas
mv /srv/nas /srv/nas.sd-old
mkdir /srv/nas

echo "== fstab"
sed -i '\#[[:space:]]/srv/nas[[:space:]]#d' /etc/fstab
echo "UUID=$UUID /srv/nas ext4 defaults,noatime,nofail,x-systemd.device-timeout=15s 0 2" >> /etc/fstab
systemctl daemon-reload
mount /srv/nas
chown som:som /srv/nas

echo "== verify copy"
diff -r /srv/nas.sd-old /srv/nas && echo "copy identical"

echo "== starting services"
systemctl start musik picloud pihub syncthing@som
sleep 3
for u in musik picloud pihub syncthing@som; do echo "$u $(systemctl is-active $u)"; done
df -h /srv/nas | tail -1
