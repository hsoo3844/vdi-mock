#!/usr/bin/env bash
# 모든 K8s 노드 공통 준비 (Ubuntu 24.04): swap off, 루트 LV 확장, 모듈/sysctl, containerd, kubeadm v1.36
# 사용: sudo bash node-prep.sh   (master / worker 3대 모두)
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
K8S_MINOR=v1.36

echo "### 1. swap off"
swapoff -a
sed -i -E 's@^([^#].*\sswap\s.*)$@# \1@' /etc/fstab
rm -f /swap.img

echo "### 2. 루트 LV를 디스크 전체로 확장 (Ubuntu 설치 기본은 절반만 할당)"
lvextend -r -l +100%FREE /dev/ubuntu-vg/ubuntu-lv || true
df -h / | tail -1

echo "### 3. /etc/hosts"
sed -i '/# k8s-nodes/,/# end k8s-nodes/d' /etc/hosts
cat >> /etc/hosts <<'EOF'
# k8s-nodes
172.30.0.21 master
172.30.0.22 worker1
172.30.0.23 worker2
# end k8s-nodes
EOF

echo "### 4. 커널 모듈 / sysctl"
cat > /etc/modules-load.d/k8s.conf <<'EOF'
overlay
br_netfilter
EOF
modprobe overlay
modprobe br_netfilter
cat > /etc/sysctl.d/99-k8s.conf <<'EOF'
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
EOF
sysctl --system >/dev/null

echo "### 5. containerd"
apt-get update -q
apt-get install -y -q containerd apt-transport-https ca-certificates curl gpg qemu-guest-agent >/dev/null
mkdir -p /etc/containerd
containerd config default > /etc/containerd/config.toml
sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
systemctl restart containerd
systemctl enable --now containerd qemu-guest-agent >/dev/null 2>&1 || true
containerd --version

echo "### 6. kubeadm / kubelet / kubectl ${K8S_MINOR}"
mkdir -p /etc/apt/keyrings
curl -fsSL "https://pkgs.k8s.io/core:/stable:/${K8S_MINOR}/deb/Release.key" | gpg --dearmor --yes -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/${K8S_MINOR}/deb/ /" > /etc/apt/sources.list.d/kubernetes.list
apt-get update -q
# v1.36 패키지는 cri-tools(crictl)를 자동으로 설치하지 않으므로 명시
apt-get install -y -q kubelet kubeadm kubectl cri-tools >/dev/null
apt-mark hold kubelet kubeadm kubectl >/dev/null
systemctl enable kubelet >/dev/null 2>&1
crictl config --set runtime-endpoint=unix:///run/containerd/containerd.sock >/dev/null
kubeadm version -o short

echo "### PREP DONE on $(hostname)"
