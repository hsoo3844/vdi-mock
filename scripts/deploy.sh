#!/usr/bin/env bash
# master 노드에서 실행: ./scripts/deploy.sh
# 사전 조건: 모든 worker의 containerd에 vdi-portal:0.1.0 이미지 import (scripts/build-portal.sh)
set -euo pipefail
cd "$(dirname "$0")/../manifests"

kubectl apply -f 00-namespace-rbac.yaml

# StorageClass (PostgreSQL PVC용)
kubectl get sc local-path >/dev/null 2>&1 || \
  kubectl apply -f https://raw.githubusercontent.com/rancher/local-path-provisioner/v0.0.31/deploy/local-path-storage.yaml

# 키는 클러스터 안에서만 생성·보관 (repo에 넣지 않음). 없는 키만 추가
kubectl -n vdi-dev get secret vdi-secrets >/dev/null 2>&1 || kubectl -n vdi-dev create secret generic vdi-secrets
for key in guac-json-secret-key portal-session-key db-password; do
  if [ -z "$(kubectl -n vdi-dev get secret vdi-secrets -o jsonpath="{.data.$key}")" ]; then
    kubectl -n vdi-dev patch secret vdi-secrets -p "{\"stringData\":{\"$key\":\"$(openssl rand -hex 16)\"}}"
  fi
done

helm repo add traefik https://traefik.github.io/charts >/dev/null 2>&1 || true
helm repo update traefik >/dev/null
helm upgrade --install traefik traefik/traefik -n traefik --create-namespace -f traefik-values.yaml

kubectl apply -f 05-postgres.yaml -f 10-guacamole.yaml -f 20-portal.yaml -f 30-networkpolicy.yaml -f 40-ingress.yaml
kubectl -n vdi-dev rollout status statefulset/postgres deploy/guacd deploy/guacamole deploy/vdi-portal --timeout=300s
