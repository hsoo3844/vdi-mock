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

## 접속

```bash
ssh -J root@<Proxmox Tailscale IP> master@172.30.0.21
kubectl get nodes
```

## 되돌리기

Proxmox 호스트에서 (VM을 끈 뒤):

```bash
qm rollback 100 k8s-ready   # 또는 pre-k8s
```

## 구축하며 겪은 것

- 설치 시 IP 미설정 → VM이 네트워크 없이 부팅. netplan 인터페이스 이름 오타(`enp18`)로 적용 안 됨 → `ens18`
- Ubuntu 설치 기본 LVM은 디스크 절반만 루트에 할당 → `lvextend -r -l +100%FREE`
- v1.36 패키지는 `cri-tools`(crictl)를 같이 설치하지 않음 → 명시 설치
- Calico 기본 custom-resources는 `192.168.0.0/16` + `VXLANCrossSubnet` → 직접 작성
