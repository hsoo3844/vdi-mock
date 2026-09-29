#!/usr/bin/env bash
# master 노드에서 실행: 포털 이미지를 빌드하고 worker containerd에 import (레지스트리 없는 목업용)
# 사용: ./scripts/build-portal.sh [worker ...]   (기본: worker1 worker2, SSH 접속 가능해야 함)
set -euo pipefail
TAG=vdi-portal:0.1.0
WORKERS=("${@:-worker1 worker2}")
cd "$(dirname "$0")/../portal"

docker build -t "$TAG" .
docker save "$TAG" -o /tmp/vdi-portal.tar
for w in ${WORKERS[@]}; do
  scp /tmp/vdi-portal.tar "$w:/tmp/vdi-portal.tar"
  ssh -t "$w" "sudo ctr -n k8s.io images import /tmp/vdi-portal.tar"
done
