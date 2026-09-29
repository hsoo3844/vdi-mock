#!/usr/bin/env bash
# master: kubeadm init + kubeconfig + Calico(VXLAN Always, MTU 1450) + helm
# 사용: sudo bash master-init.sh   → 마지막 줄의 JOIN 명령을 각 worker에서 sudo로 실행
set -euo pipefail
CALICO=v3.32.2
ADMIN_USER=master

if [ ! -f /etc/kubernetes/admin.conf ]; then
  kubeadm config images pull --kubernetes-version v1.36.5 >/dev/null
  kubeadm init \
    --kubernetes-version v1.36.5 \
    --apiserver-advertise-address 172.30.0.21 \
    --control-plane-endpoint 172.30.0.21:6443 \
    --pod-network-cidr 10.244.0.0/16 \
    --service-cidr 10.96.0.0/12 \
    --node-name master | tail -4
fi

# kubeconfig: root + master 사용자
mkdir -p /root/.kube && cp /etc/kubernetes/admin.conf /root/.kube/config
H=$(getent passwd "$ADMIN_USER" | cut -d: -f6)
mkdir -p "$H/.kube" && cp /etc/kubernetes/admin.conf "$H/.kube/config" && chown -R "$ADMIN_USER:$ADMIN_USER" "$H/.kube"
export KUBECONFIG=/etc/kubernetes/admin.conf

# Calico (operator)
B=https://raw.githubusercontent.com/projectcalico/calico/$CALICO/manifests
kubectl apply --server-side -f $B/operator-crds.yaml >/dev/null
kubectl apply --server-side -f $B/tigera-operator.yaml >/dev/null
kubectl -n tigera-operator rollout status deploy/tigera-operator --timeout=180s
cat <<'EOF' | kubectl apply -f -
apiVersion: operator.tigera.io/v1
kind: Installation
metadata:
  name: default
spec:
  calicoNetwork:
    mtu: 1450                 # Proxmox VM 위 VXLAN (설계 4장)
    ipPools:
      - name: default-ipv4-ippool
        blockSize: 26
        cidr: 10.244.0.0/16    # 기본값 192.168.0.0/16은 강의실 LAN과 충돌
        encapsulation: VXLAN   # Always
        natOutgoing: Enabled
        nodeSelector: all()
---
apiVersion: operator.tigera.io/v1
kind: APIServer
metadata:
  name: default
spec: {}
EOF

# helm
if ! command -v helm >/dev/null; then
  curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash >/dev/null
fi
helm version --short

echo "JOIN: $(kubeadm token create --print-join-command)"
