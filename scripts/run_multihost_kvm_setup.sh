#!/usr/bin/env bash
# Relaunch VM1 (killed by run_multihost_kvm_raft_consensus_probe.py as part
# of the cross-host failure test) so the probe can be repeated. VM2 is left
# untouched -- only its raft containers get cleaned up by the probe itself.
set -euo pipefail
cd "$(dirname "$0")/.."
VMDIR=".multihost_vm"

if [ -f "$VMDIR/vm1.pid" ] && kill -0 "$(cat "$VMDIR/vm1.pid")" 2>/dev/null; then
  echo "vm1 already running"
else
  rm -f "$VMDIR/vm1.pid" "$VMDIR/vm1-monitor.sock"
  sg kvm -c "
    qemu-system-x86_64 \
      -name vm1 -m 2048 -smp 2 -enable-kvm -cpu host \
      -drive file=$VMDIR/vm1.qcow2,if=virtio,format=qcow2 \
      -drive file=$VMDIR/vm1-seed.iso,if=virtio,format=raw \
      -netdev user,id=net0,hostfwd=tcp::12201-:22 \
      -device virtio-net-pci,netdev=net0,mac=52:54:00:12:34:01 \
      -netdev socket,id=net1,mcast=230.77.0.1:5077 \
      -device virtio-net-pci,netdev=net1,mac=52:54:00:12:34:11 \
      -display none -vga none \
      -serial file:$PWD/$VMDIR/vm1-serial.log \
      -daemonize -pidfile $PWD/$VMDIR/vm1.pid \
      -monitor unix:$PWD/$VMDIR/vm1-monitor.sock,server,nowait
  "
fi

until ssh -i "$VMDIR/id_multihost" -p 12201 -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null -o ConnectTimeout=3 ubuntu@127.0.0.1 "true" 2>/dev/null; do
  sleep 2
done

# Wait for the private link to VM2 to actually come back up too, not just
# SSH -- the socket netdev takes a moment to re-establish after a fresh
# boot even once sshd is answering.
until ssh -i "$VMDIR/id_multihost" -p 12201 -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null -o ConnectTimeout=3 ubuntu@127.0.0.1 \
    "ping -c 1 -W 1 10.77.0.12 >/dev/null 2>&1"; do
  sleep 2
done

echo "vm1 ready"
