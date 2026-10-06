# kubespray — K8s 자동 구축 (CollabOps #14)

정본 5장 "kubeadm 수동 → kubespray 자동화". kubespray **v2.32.0** (2026-09-22, K8s 1.36.5 지원, ansible==12.3.0).

| 항목 | 값 |
|---|---|
| 노드 | master .21 (control-plane + etcd), worker1 .22, worker2 .23 |
| K8s | 1.36.4, containerd (v2.32.0 지원 최신) |
| CNI | **Calico** (2026-10-06 팀 결정), VXLAN Always, IPIP Never, MTU 1450, natOutgoing |
| 주소 | Pod 10.244.0.0/16, Service 10.96.0.0/12 |
| 애드온 | 없음 (nodelocaldns·helm·metrics-server·ingress·cert-manager·argocd·local-path 끔) |
| 실행 위치 | Actions Runner VM .30 |

## Runner 디렉터리 구조 (actions-runner@172.30.0.30, 2026-10-06 설정 완료)

OpenStack 자동화도 같은 Runner에 들어오므로 도구마다 venv·SSH 키를 분리한다 (kubespray는 ansible 12.3.0 고정 → kolla-ansible과 같은 venv에서 충돌).

```
~/devoops/
├── README.md
├── k8s/
│   ├── kubespray/   # upstream v2.32.0 (수정 금지)
│   ├── venv/        # kubespray 전용
│   ├── inventory/   # sample + hosts.yaml + group_vars/k8s_cluster/zz-devoops.yml
│   ├── logs/        # 실행 로그 (START/END = 재구축 시간)
│   └── run.sh       # ping | cluster | scale <node> | reset
├── openstack/       # kolla-ansible 전용 (예정)
└── terraform/{proxmox,openstack}/   # state 분리 (예정)
~/.ssh/id_ed25519_k8s   # K8s 노드용 키 (노드 3대에 등록됨)
```

```bash
~/devoops/k8s/run.sh ping            # 접속 확인
~/devoops/k8s/run.sh cluster         # 구축 (백그라운드, logs/cluster-*.log)
~/devoops/k8s/run.sh scale worker2   # 워커 재가입
```

## Runner 준비 (처음부터 할 때)

```bash
sudo apt-get update && sudo apt-get install -y git python3-venv
git clone --depth 1 --branch v2.32.0 https://github.com/kubernetes-sigs/kubespray.git ~/kubespray
python3 -m venv ~/kubespray-venv
~/kubespray-venv/bin/pip install -U pip
~/kubespray-venv/bin/pip install -r ~/kubespray/requirements.txt

# sample 복사 후 팀 인벤토리 덮어쓰기
cp -r ~/kubespray/inventory/sample ~/kubespray/inventory/devoops
cp hosts.yaml ~/kubespray/inventory/devoops/
cp group_vars/k8s_cluster/zz-devoops.yml ~/kubespray/inventory/devoops/group_vars/k8s_cluster/
```

노드 쪽: Runner의 SSH 공개 키를 각 노드 사용자(`master`, `worker1`, `worker2`)의 `authorized_keys`에 등록하고, 각 사용자에게 비밀번호 없는 sudo(`/etc/sudoers.d/90-kubespray`)를 준다.

## 실행

```bash
source ~/kubespray-venv/bin/activate
cd ~/kubespray
ansible -i inventory/devoops/hosts.yaml all -m ping            # 접속 확인
ansible-playbook -i inventory/devoops/hosts.yaml cluster.yml   # 구축 (소요 시간 기록)
ansible-playbook -i inventory/devoops/hosts.yaml scale.yml --limit worker2   # 워커 재가입
```

검증 전에는 VM 200~202를 Proxmox 스냅샷 `pre-k8s`로 되돌려 빈 상태에서 시작한다.
되돌리면 노드의 NOPASSWD sudo와 authorized_keys도 사라지므로 부트스트랩(키 등록, `/etc/sudoers.d/90-kubespray`)부터 다시 한다.

## 첫 실행 결과 (2026-10-06)

- 실행 위치: master(.21) 임시 사용 (Runner 계정 미확인). 같은 venv·인벤토리를 Runner로 옮기면 된다.
- `cluster.yml` **6분 42초** (08:06:19 → 08:13:01), failed 0. 로그의 `fatal` 4줄은 첫 실행 존재 확인용(`ignored=4`).
- 결과: 노드 3대 Ready, **K8s 1.36.4** (kubespray v2.32.0 checksums는 1.36.4까지 — 1.36.5는 거부됨), containerd 2.3.5, Calico 풀 10.244.0.0/16 VXLAN Always / IPIP Never / NAT / blockSize 26, vxlan.calico MTU 1450, kube-proxy iptables, CoreDNS 10.96.0.3, 노드 간 VXLAN 터널 ping OK.
- 기본으로 함께 올라오는 것: `dns-autoscaler`(CoreDNS 개수 조절). etcd는 master의 systemd 서비스.
- kubespray는 kubeconfig를 root에만 둔다 → `sudo cp /etc/kubernetes/admin.conf ~/.kube/config`.
- 스냅샷: `kubespray-ready`.
