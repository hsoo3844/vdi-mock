# VDI mock (노트북 VirtualBox + K8s + Guacamole)

실제 프로젝트(OS 선택형 VDI, Proxmox + OpenStack + K8s)의 **서비스 계층을 노트북에서 미리 검증**하기 위한 목업.
OpenStack VM 대신 xrdp 컨테이너 Pod를 "데스크톱"으로 쓰고, 나머지 흐름(포털 API → 생성 → RDP 응답 대기 → Guacamole JSON 토큰 → 브라우저 접속 → 반납)은 실제 설계와 동일하게 맞췄다.

## 접속

- 포털: http://localhost:30080/  (사용자 이름만 입력하면 로그인)
- Guacamole: http://localhost:30080/guacamole/  (포털의 "접속" 버튼으로만 로그인, 직접 로그인 불가)

## 구성

```
노트북 :30080 ─(VirtualBox NAT Network 포트포워딩 vdi-http)─> worker1 192.168.0.102:30080
  └ Traefik (NodePort 30080, worker1 고정)
      ├ /           → vdi-portal (FastAPI)  ── K8s API로 데스크톱 Pod/Service 생성·삭제
      └ /guacamole  → guacamole(web) ── guacd ──RDP 3389──> desk-<id> Pod (xrdp)
```

| VM | IP (NAT Network `Nat`) | SSH (노트북) | 역할 |
|---|---|---|---|
| master | 192.168.0.101 | localhost:10022 | control-plane, 이미지 빌드(docker) |
| worker1 | 192.168.0.102 | localhost:20022 | Traefik, 워크로드 |
| worker2 | 192.168.0.103 | localhost:30022 | 워크로드 |

K8s v1.30.14 (kubeadm), Calico (Pod CIDR 10.244.0.0/16), 네임스페이스 `vdi-dev` / `traefik`.

## 실제 설계와의 대응

| 실제 (PROJECT_CONTEXT) | 목업 |
|---|---|
| OpenStack VM (Nova) | `desk-<id>` Pod + Service (`lscr.io/linuxserver/rdesktop`) |
| Glance 이미지 = OS 선택지 | Ubuntu XFCE / MATE / IceWM (Rocky·Debian·Windows는 실환경에서) |
| 사용자별 Neutron 망 + 보안그룹(워커발 RDP만) | NetworkPolicy: 데스크톱은 guacd발 3389만 허용 |
| ACTIVE 말고 RDP 응답으로 READY 판정 | readinessProbe tcpSocket 3389 |
| Linux 5분 / Windows 10분 타임아웃 → 롤백 | `VDI_CREATE_TIMEOUT_SEC`(600) 초과 시 ERROR → 삭제 |
| guacamole-auth-json 토큰 | 동일 (HMAC-SHA256 서명 + AES-128-CBC) |
| guacd / guacamole 분리 Deployment | 동일 |
| Traefik v3, WebSocket 타임아웃 확장 | 동일 (readTimeout 0, idleTimeout 3600s) |
| OpenStack 연동 모듈 인터페이스 분리 | `KubernetesDesktopBackend` (create / delete / list / status) → OpenStack 구현으로 교체 |
| 할당량 초과 409, 생성 202 비동기 | 동일 (사용자당 2대) |
| `/metrics` | `vdi_desktops{status}`, `vdi_desktops_created_total` 등 |

목업에서 빠진 것: 로그인 비밀번호, DB(상태는 Pod 라벨에서 계산), IN_USE/IDLE 판정과 30분 자동 반납, TLS, CI/CD(ArgoCD).

## 배포 / 재배포

master에서 (`~/vdi-mock`에 이 repo 복사):

```bash
./scripts/build-portal.sh      # 포털 이미지 빌드 → worker containerd import (포털 코드 바꿨을 때)
./scripts/deploy.sh            # Secret(최초 1회 랜덤 생성), Traefik(helm), 매니페스트 적용
kubectl -n vdi-dev rollout restart deploy/vdi-portal   # 이미지 교체 후
```

노트북 포트포워딩 (1회, 이미 추가됨):

```powershell
& "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe" natnetwork modify --netname Nat --port-forward-4 "vdi-http:tcp:[]:30080:[192.168.0.102]:30080"
```

## 테스트

```bash
python scripts/e2e_test.py     # 로그인 → 생성 → READY → Guacamole 토큰 → RDP 세션 → 할당량 409 → 반납
```

## 정리

```bash
kubectl delete ns vdi-dev && helm -n traefik uninstall traefik && kubectl delete ns traefik
```
```powershell
& "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe" natnetwork modify --netname Nat --port-forward-4 delete vdi-http
```

## 주의

- worker가 1 vCPU / 4GB라 데스크톱 동시 3~4대가 한계. 데스크톱 이미지는 노드당 약 1.5GB 디스크를 쓴다 (worker 여유 약 11GB).
- 데스크톱 계정은 이미지 기본값 `abc/abc` (Guacamole가 자동 입력).
