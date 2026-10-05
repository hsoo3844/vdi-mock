#!/usr/bin/env bash
# master: kubeadm init + kubeconfig + flannel(VXLAN, 10.244.0.0/16). helm·ingress·앱은 설치하지 않는다.
# 정본(CLAUDE.md 5장): CNI는 flannel로 시작 → NetworkPolicy가 필요해지면 Calico 전환 검토.
# 사용: sudo bash master-init-flannel.sh → 마지막 JOIN 명령을 각 worker에서 sudo로 실행
set -euo pipefail
K8S_VERSION=v1.36.5
FLANNEL=v0.28.9
ADMIN_USER=master

if [ ! -f /etc/kubernetes/admin.conf ]; then
  kubeadm config images pull --kubernetes-version "$K8S_VERSION" >/dev/null
  kubeadm init \
    --kubernetes-version "$K8S_VERSION" \
    --apiserver-advertise-address 172.30.0.21 \
    --control-plane-endpoint 172.30.0.21:6443 \
    --pod-network-cidr 10.244.0.0/16 \
    --service-cidr 10.96.0.0/12 \
    --node-name master | grep -E 'initialized successfully|^kubeadm join' || true
fi

mkdir -p /root/.kube && cp /etc/kubernetes/admin.conf /root/.kube/config
H=$(getent passwd "$ADMIN_USER" | cut -d: -f6)
mkdir -p "$H/.kube" && cp /etc/kubernetes/admin.conf "$H/.kube/config" && chown -R "$ADMIN_USER:$ADMIN_USER" "$H/.kube"
export KUBECONFIG=/etc/kubernetes/admin.conf

# flannel 기본 net-conf가 Network 10.244.0.0/16, Backend vxlan(UDP 8472, MTU 1450)이라 수정 없이 적용
kubectl apply -f "https://github.com/flannel-io/flannel/releases/download/${FLANNEL}/kube-flannel.yml"
kubectl -n kube-flannel get cm kube-flannel-cfg -o jsonpath='{.data.net-conf\.json}'; echo

echo "JOIN: $(kubeadm token create --print-join-command)"
