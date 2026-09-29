"""VDI mock portal.

Implements the portal REST API from PROJECT_CONTEXT (6장) against a Kubernetes
backend: each "desktop" is an xrdp container Pod + Service instead of an
OpenStack VM. The backend sits behind the same create/delete/status interface
so it can later be swapped for an OpenStack (or Proxmox) implementation.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel

import store

NAMESPACE = os.environ.get("VDI_NAMESPACE", "vdi-dev")
GUAC_JSON_KEY = bytes.fromhex(os.environ["GUAC_JSON_SECRET_KEY"])
GUAC_URL = os.environ.get("GUAC_URL", "/guacamole/")
GUAC_DATA_TTL = int(os.environ.get("GUAC_DATA_TTL_SEC", str(8 * 3600)))
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://prometheus.monitoring:9090/prometheus")
SESSION_KEY = os.environ.get("PORTAL_SESSION_KEY", secrets.token_hex(16)).encode()
CREATE_TIMEOUT = int(os.environ.get("VDI_CREATE_TIMEOUT_SEC", "600"))
DESKTOP_USER = os.environ.get("VDI_DESKTOP_USER", "abc")
DESKTOP_PASSWORD = os.environ.get("VDI_DESKTOP_PASSWORD", "abc")

# 선택 가능한 OS 목록 (실제 환경에서는 Glance 이미지 조회로 대체)
IMAGES = {
    "ubuntu-xfce": {"name": "Ubuntu XFCE", "image": "lscr.io/linuxserver/rdesktop:ubuntu-xfce",
                    "desc": "가볍고 표준적인 데스크톱. 처음이라면 이걸로."},
    "ubuntu-mate": {"name": "Ubuntu MATE", "image": "lscr.io/linuxserver/rdesktop:ubuntu-mate",
                    "desc": "익숙한 클래식 레이아웃의 풀 데스크톱."},
    "ubuntu-icewm": {"name": "Ubuntu IceWM (경량)", "image": "lscr.io/linuxserver/rdesktop:ubuntu-icewm",
                     "desc": "최소 자원으로 가장 빠르게 뜨는 경량 환경."},
}

LABEL_APP = "vdi-desktop"

desktops_created = Counter("vdi_desktops_created_total", "Desktop create requests", ["os"])
desktops_deleted = Counter("vdi_desktops_deleted_total", "Desktop delete requests")
desktops_failed = Counter("vdi_desktops_failed_total", "Desktops that timed out and were rolled back")
desktops_current = Gauge("vdi_desktops", "Desktops by status", ["status"])
http_requests = Counter("vdi_http_requests_total", "Portal API requests", ["method", "route", "code"])
http_latency = Histogram("vdi_http_request_seconds", "Portal API latency", ["route"])
desktop_ready_seconds = Histogram(
    "vdi_desktop_ready_seconds", "Time from create request to RDP ready", ["os"],
    buckets=(5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 300, 600),
)
_ready_observed = set()


# ---------------------------------------------------------------- backend


class KubernetesDesktopBackend:
    """create / delete / status backed by a Pod + Service per desktop."""

    def __init__(self):
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self.core = client.CoreV1Api()

    def create(self, desktop_id, owner, os_key):
        name = f"desk-{desktop_id}"
        labels = {"app": LABEL_APP, "vdi/id": desktop_id, "vdi/owner": owner, "vdi/os": os_key}
        pod = client.V1Pod(
            metadata=client.V1ObjectMeta(name=name, labels=labels),
            spec=client.V1PodSpec(
                hostname=name,
                containers=[
                    client.V1Container(
                        name="desktop",
                        image=IMAGES[os_key]["image"],
                        image_pull_policy="IfNotPresent",
                        env=[client.V1EnvVar(name="TZ", value="Asia/Seoul")],
                        ports=[client.V1ContainerPort(container_port=3389, name="rdp")],
                        # Running만으로 READY 판정 금지 → RDP 포트 응답으로 판정
                        readiness_probe=client.V1Probe(
                            tcp_socket=client.V1TCPSocketAction(port=3389),
                            period_seconds=5,
                        ),
                        resources=client.V1ResourceRequirements(
                            requests={"cpu": "100m", "memory": "256Mi"},
                            limits={"memory": "1536Mi"},
                        ),
                        volume_mounts=[client.V1VolumeMount(name="shm", mount_path="/dev/shm")],
                    )
                ],
                volumes=[
                    client.V1Volume(
                        name="shm",
                        empty_dir=client.V1EmptyDirVolumeSource(medium="Memory", size_limit="512Mi"),
                    )
                ],
            ),
        )
        svc = client.V1Service(
            metadata=client.V1ObjectMeta(name=name, labels=labels),
            spec=client.V1ServiceSpec(
                selector={"vdi/id": desktop_id},
                ports=[client.V1ServicePort(name="rdp", port=3389, target_port=3389)],
            ),
        )
        self.core.create_namespaced_pod(NAMESPACE, pod)
        try:
            self.core.create_namespaced_service(NAMESPACE, svc)
        except ApiException:
            self.delete(desktop_id)
            raise

    def delete(self, desktop_id):
        # 역순 삭제: 연결(Service) → 데스크톱(Pod)
        name = f"desk-{desktop_id}"
        for fn in (self.core.delete_namespaced_service, self.core.delete_namespaced_pod):
            try:
                fn(name, NAMESPACE)
            except ApiException as e:
                if e.status != 404:
                    raise

    def list(self, owner=None):
        selector = f"app={LABEL_APP}" + (f",vdi/owner={owner}" if owner else "")
        pods = self.core.list_namespaced_pod(NAMESPACE, label_selector=selector).items
        return [self._to_desktop(p) for p in pods]

    def status(self, desktop_id):
        pods = self.core.list_namespaced_pod(NAMESPACE, label_selector=f"vdi/id={desktop_id}").items
        return self._to_desktop(pods[0]) if pods else None

    def _to_desktop(self, pod):
        labels = pod.metadata.labels
        created = pod.metadata.creation_timestamp
        ready_cond = next((c for c in (pod.status.conditions or []) if c.type == "Ready" and c.status == "True"), None)
        ready = ready_cond is not None
        age = (datetime.now(timezone.utc) - created).total_seconds()
        if pod.metadata.deletion_timestamp:
            status = "DELETING"
        elif ready:
            status = "READY"
        elif pod.status.phase == "Failed" or age > CREATE_TIMEOUT:
            status = "ERROR"
        else:
            status = "CREATING"
        # 생성 단계 (UI 진행 표시용): 노드 배치 → 이미지 준비 → RDP 응답 대기 → 준비 완료
        container = (pod.status.container_statuses or [None])[0]
        if ready:
            stage = "ready"
        elif not pod.spec.node_name:
            stage = "scheduling"
        elif container and container.state and container.state.running:
            stage = "booting"
        else:
            stage = "pulling"
        return {
            "id": labels["vdi/id"],
            "owner": labels["vdi/owner"],
            "os": labels["vdi/os"],
            "os_name": IMAGES.get(labels["vdi/os"], {}).get("name", labels["vdi/os"]),
            "status": status,
            "stage": stage,
            "node": pod.spec.node_name,
            "created_at": created.isoformat(),
            "ready_seconds": (ready_cond.last_transition_time - created).total_seconds() if ready else None,
        }


backend = KubernetesDesktopBackend()

# ---------------------------------------------------------------- guacamole


def guacamole_data(username, desktop):
    """Encrypted guacamole-auth-json payload (HMAC-SHA256 sign + AES-128-CBC)."""
    payload = {
        "username": username,
        # 만료 후엔 세션에서 연결이 사라져 Guacamole 자동 재연결이 실패한다 → 사용 세션 길이만큼
        "expires": int((time.time() + GUAC_DATA_TTL) * 1000),
        "connections": {
            # 연결 이름 = Guacamole 식별자. UI가 btoa()로 인코딩하므로 ASCII만 허용
            # (한글이 들어가면 InvalidCharacterError → 흰 화면)
            f"{desktop['os']}-{desktop['id']}": {
                "protocol": "rdp",
                "parameters": {
                    "hostname": f"desk-{desktop['id']}.{NAMESPACE}.svc.cluster.local",
                    "port": "3389",
                    "username": DESKTOP_USER,
                    "password": DESKTOP_PASSWORD,
                    "security": "any",
                    "ignore-cert": "true",
                    "resize-method": "display-update",
                    "timezone": "Asia/Seoul",
                },
            }
        },
    }
    body = json.dumps(payload).encode()
    signed = hmac.new(GUAC_JSON_KEY, body, hashlib.sha256).digest() + body
    padder = padding.PKCS7(128).padder()
    padded = padder.update(signed) + padder.finalize()
    enc = Cipher(algorithms.AES(GUAC_JSON_KEY), modes.CBC(b"\0" * 16)).encryptor()
    return base64.b64encode(enc.update(padded) + enc.finalize()).decode()


# ---------------------------------------------------------------- auth (mock)


def issue_token(username):
    sig = hmac.new(SESSION_KEY, username.encode(), hashlib.sha256).hexdigest()
    return f"{username}.{sig}"


def current_user(authorization: str = Header(default="")):
    """Valid token + user still exists and is not disabled (비활성화 즉시 차단)."""
    token = authorization.removeprefix("Bearer ").strip()
    username, _, sig = token.rpartition(".")
    if not username or not hmac.compare_digest(sig, issue_token(username).rpartition(".")[2]):
        raise HTTPException(401, "login required")
    user = store.get_user(username)
    if not user or user["disabled"]:
        raise HTTPException(401, "account disabled or removed")
    return user


def require_admin(user: dict = Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "admin only")
    return user


def valid_username(name):
    name = name.strip().lower()
    if not name.isalnum() or not name.isascii() or len(name) > 20:
        raise HTTPException(400, "username must be 1-20 alphanumeric characters")
    return name


# ---------------------------------------------------------------- api

app = FastAPI(title="VDI mock portal")


@app.on_event("startup")
def startup():
    store.init()


class LoginRequest(BaseModel):
    username: str


class CreateRequest(BaseModel):
    os: str


def _create(owner, os_key, actor, quota):
    if os_key not in IMAGES:
        raise HTTPException(400, "unknown os")
    active = [d for d in backend.list(owner) if d["status"] != "DELETING"]
    if quota is not None and len(active) >= quota:
        raise HTTPException(409, f"quota exceeded ({quota} desktops per user)")
    desktop_id = secrets.token_hex(3)
    backend.create(desktop_id, owner, os_key)
    desktops_created.labels(os_key).inc()
    store.log(actor, "desktop.create", owner, f"{desktop_id} {os_key}")
    return {"id": desktop_id, "owner": owner, "status": "CREATING"}


def _delete(desktop_id, owner, actor):
    backend.delete(desktop_id)
    desktops_deleted.inc()
    store.log(actor, "desktop.delete", owner, desktop_id)
    return {"id": desktop_id, "status": "DELETING"}


@app.post("/api/auth/login")
def login(req: LoginRequest):
    username = valid_username(req.username)
    user = store.login(username)
    if not user:
        raise HTTPException(403, "account disabled")
    store.log(username, "login")
    return {"token": issue_token(username), "username": username, "role": user["role"]}


@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    return user


@app.get("/api/images")
def images():
    return [{"id": k, "name": v["name"], "desc": v["desc"]} for k, v in IMAGES.items()]


@app.post("/api/desktops", status_code=202)
def create_desktop(req: CreateRequest, user: dict = Depends(current_user)):
    return _create(user["username"], req.os, user["username"], user["quota"])


@app.get("/api/desktops")
def list_desktops(user: dict = Depends(current_user)):
    result = backend.list(user["username"])
    for d in result:
        if d["status"] == "ERROR":  # 타임아웃 → 롤백
            desktops_failed.inc()
            backend.delete(d["id"])
            store.log("system", "desktop.rollback", d["owner"], d["id"])
    return result


def _owned(desktop_id, user):
    d = backend.status(desktop_id)
    if not d or d["owner"] != user["username"]:
        raise HTTPException(404, "not found")
    return d


@app.get("/api/desktops/{desktop_id}")
def get_desktop(desktop_id: str, user: dict = Depends(current_user)):
    return _owned(desktop_id, user)


@app.post("/api/desktops/{desktop_id}/connect")
def connect(desktop_id: str, user: dict = Depends(current_user)):
    d = _owned(desktop_id, user)
    if d["status"] != "READY":
        raise HTTPException(409, f"desktop is {d['status']}")
    store.log(user["username"], "desktop.connect", user["username"], desktop_id)
    data = urllib.parse.quote(guacamole_data(user["username"], d), safe="")
    return {"url": f"{GUAC_URL}?data={data}"}


@app.delete("/api/desktops/{desktop_id}", status_code=202)
def delete_desktop(desktop_id: str, user: dict = Depends(current_user)):
    _owned(desktop_id, user)
    return _delete(desktop_id, user["username"], user["username"])


# ---------------------------------------------------------------- admin api


class AdminUserCreate(BaseModel):
    username: str
    role: str = "user"
    quota: Optional[int] = None


class AdminUserUpdate(BaseModel):
    role: Optional[str] = None
    quota: Optional[int] = None
    disabled: Optional[bool] = None


class AdminDesktopCreate(BaseModel):
    owner: str
    os: str


def _check_role(role):
    if role is not None and role not in ("user", "admin"):
        raise HTTPException(400, "role must be user or admin")


@app.get("/api/admin/summary")
def admin_summary(_: dict = Depends(require_admin)):
    desktops = backend.list()
    users = store.list_users()

    def count(key):
        out = {}
        for d in desktops:
            out[d[key] or "-"] = out.get(d[key] or "-", 0) + 1
        return out

    return {
        "users": len(users),
        "admins": sum(u["role"] == "admin" for u in users),
        "disabled": sum(u["disabled"] for u in users),
        "desktops": len(desktops),
        "by_status": count("status"),
        "by_os": count("os_name"),
        "by_node": count("node"),
    }


@app.get("/api/admin/users")
def admin_users(_: dict = Depends(require_admin)):
    per_user = {}
    for d in backend.list():
        per_user[d["owner"]] = per_user.get(d["owner"], 0) + 1
    return [{**u, "desktops": per_user.get(u["username"], 0)} for u in store.list_users()]


@app.post("/api/admin/users", status_code=201)
def admin_create_user(req: AdminUserCreate, admin: dict = Depends(require_admin)):
    _check_role(req.role)
    username = valid_username(req.username)
    user = store.create_user(username, req.role, req.quota)
    if not user:
        raise HTTPException(409, "user already exists")
    store.log(admin["username"], "user.create", username, f"role={req.role}")
    return user


@app.patch("/api/admin/users/{username}")
def admin_update_user(username: str, req: AdminUserUpdate, admin: dict = Depends(require_admin)):
    _check_role(req.role)
    if req.quota is not None and req.quota < 0:
        raise HTTPException(400, "quota must be >= 0")
    target = store.get_user(username)
    if not target:
        raise HTTPException(404, "not found")
    losing_admin = target["role"] == "admin" and not target["disabled"] and (req.role == "user" or req.disabled)
    if losing_admin and store.count_active_admins() <= 1:
        raise HTTPException(409, "at least one active admin is required")
    user = store.update_user(username, req.role, req.quota, req.disabled)
    changes = ", ".join(f"{k}={v}" for k, v in req.model_dump(exclude_none=True).items())
    store.log(admin["username"], "user.update", username, changes)
    return user


@app.delete("/api/admin/users/{username}")
def admin_delete_user(username: str, admin: dict = Depends(require_admin)):
    target = store.get_user(username)
    if not target:
        raise HTTPException(404, "not found")
    if username == admin["username"]:
        raise HTTPException(409, "cannot delete yourself")
    if target["role"] == "admin" and not target["disabled"] and store.count_active_admins() <= 1:
        raise HTTPException(409, "at least one active admin is required")
    reclaimed = [d["id"] for d in backend.list(username)]
    for desktop_id in reclaimed:
        _delete(desktop_id, username, admin["username"])
    store.delete_user(username)
    store.log(admin["username"], "user.delete", username, f"reclaimed={len(reclaimed)}")
    return {"username": username, "reclaimed": reclaimed}


@app.get("/api/admin/desktops")
def admin_desktops(owner: Optional[str] = None, _: dict = Depends(require_admin)):
    return backend.list(owner)


@app.post("/api/admin/desktops", status_code=202)
def admin_create_desktop(req: AdminDesktopCreate, admin: dict = Depends(require_admin)):
    owner = store.get_user(req.owner)
    if not owner:
        raise HTTPException(404, "user not found")
    # 관리자 할당은 할당량을 무시한다
    return _create(owner["username"], req.os, admin["username"], None)


@app.delete("/api/admin/desktops/{desktop_id}", status_code=202)
def admin_delete_desktop(desktop_id: str, admin: dict = Depends(require_admin)):
    d = backend.status(desktop_id)
    if not d:
        raise HTTPException(404, "not found")
    return _delete(desktop_id, d["owner"], admin["username"])


@app.get("/api/admin/events")
def admin_events(limit: int = 100, username: Optional[str] = None, _: dict = Depends(require_admin)):
    return store.events(min(limit, 500), username)


@app.get("/api/admin/prom/{kind}")
def admin_prom(
    kind: str,
    query: str,
    start: Optional[float] = None,
    end: Optional[float] = None,
    step: Optional[float] = None,
    _: dict = Depends(require_admin),
):
    """Read-only relay to the Prometheus HTTP API (query / query_range only)."""
    if kind not in ("query", "query_range"):
        raise HTTPException(404, "not found")
    params = {"query": query}
    if kind == "query_range":
        now = time.time()
        params.update(start=start or now - 900, end=end or now, step=step or 15)
    url = f"{PROMETHEUS_URL}/api/v1/{kind}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise HTTPException(e.code, e.read().decode(errors="replace")[:300])
    except (urllib.error.URLError, TimeoutError):
        raise HTTPException(503, "prometheus unavailable")


# ---------------------------------------------------------------- misc


@app.middleware("http")
async def record_requests(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    path = getattr(route, "path", "other")
    if path.startswith("/api"):
        http_requests.labels(request.method, path, str(response.status_code)).inc()
        http_latency.labels(path).observe(time.perf_counter() - started)
    return response


@app.get("/metrics")
def metrics():
    counts = {}
    for d in backend.list():
        counts[d["status"]] = counts.get(d["status"], 0) + 1
        if d["ready_seconds"] is not None and d["id"] not in _ready_observed:
            _ready_observed.add(d["id"])
            desktop_ready_seconds.labels(d["os"]).observe(d["ready_seconds"])
    for s in ("CREATING", "READY", "DELETING", "ERROR"):
        desktops_current.labels(s).set(counts.get(s, 0))
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/healthz")
def healthz():
    return {"ok": True}


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/admin")
def admin_page():
    return FileResponse("static/admin.html")

