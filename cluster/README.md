# cluster — Proxmox 테스트 K8s 구축

팀 Proxmox(PVE 9.2) 위 VM 3대에 kubeadm으로 올린 테스트 클러스터. 2026-09-29 구축.

| VM ID | 노드 | IP | vCPU / RAM / Disk |
|---|---|---|---|
| 100 | master (control-plane) | 172.30.0.21 | 2 / 4GB / 40GB |
| 101 | worker1 | 172.30.0.22 | 3 / 6GB / 50GB |
| 102 | worker2 | 172.30.0.23 | 3 / 6GB / 50GB |

- Ubuntu 24.04.5, Kubernetes **v1.36.5**, containerd 2.2.1, helm v3.22
- Calico **v3.32.2** (operator): Pod CIDR `10.244.0.0/16`, **VXLAN Always**, natOutgoing, MTU **1450**
- Service CIDR `10.96.0.0/12`, 게이트웨이 172.30.0.1 (Proxmox 호스트 MASQUERADE)

## 순서

1. 각 VM 고정 IP (netplan, 인터페이스 `ens18`) — 설계 4장 주소표
2. Proxmox 스냅샷 `pre-k8s`
3. 3대 모두 `sudo bash node-prep.sh`
4. master에서 `sudo bash master-init.sh` → 출력된 `JOIN:` 명령을 worker 2대에서 sudo로 실행
5. 스냅샷 `k8s-ready`
6. 목업 배포 후 스냅샷 `vdi-deployed`

## 접속

```bash
ssh -J root@<Proxmox Tailscale IP> master@172.30.0.21
kubectl get nodes
```

## VDI 목업 배포 (시연용)

```bash
# worker 2대: 포털 이미지 import (레지스트리 없음)
sudo ctr -n k8s.io images import /tmp/vdi-portal.tar
# master: 레포 루트에서
./scripts/deploy.sh
```

- 노드 이름이 `worker1`이라 `traefik-values.yaml` 수정 없이 Traefik이 worker1에 뜬다 (NodePort 30080).
- Proxmox 호스트 DNAT (`/etc/network/interfaces`의 vmbr1 `post-up`, 원본은 `interfaces.bak-before-vdi-dnat`):
  `iptables -t nat -A PREROUTING -i tailscale0 -d <호스트 Tailscale IP> -p tcp --dport 80 -j DNAT --to-destination 172.30.0.22:30080`
- 접속: `http://<호스트 Tailscale IP>/` (포털), `/admin` (관리자, 이름 `admin`), `/prometheus/`
  — Tailscale로 호스트가 공유된 사람만 접근 가능. 강의실 LAN·인터넷에는 열려 있지 않다.

## 되돌리기

Proxmox 호스트에서 (VM을 끈 뒤):

```bash
qm rollback 100 vdi-deployed   # 또는 k8s-ready, pre-k8s (101, 102도 같이)
```

## 구축하며 겪은 것

- 설치 시 IP 미설정 → VM이 네트워크 없이 부팅. netplan 인터페이스 이름 오타(`enp18`)로 적용 안 됨 → `ens18`
- Ubuntu 설치 기본 LVM은 디스크 절반만 루트에 할당 → `lvextend -r -l +100%FREE`
- v1.36 패키지는 `cri-tools`(crictl)를 같이 설치하지 않음 → 명시 설치
- Calico 기본 custom-resources는 `192.168.0.0/16` + `VXLANCrossSubnet` → 직접 작성
