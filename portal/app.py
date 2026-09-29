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
import urllib.parse
from datetime import datetime, timezone

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest
from pydantic import BaseModel

NAMESPACE = os.environ.get("VDI_NAMESPACE", "vdi-dev")
GUAC_JSON_KEY = bytes.fromhex(os.environ["GUAC_JSON_SECRET_KEY"])
GUAC_URL = os.environ.get("GUAC_URL", "/guacamole/")
SESSION_KEY = os.environ.get("PORTAL_SESSION_KEY", secrets.token_hex(16)).encode()
QUOTA = int(os.environ.get("VDI_QUOTA_PER_USER", "2"))
CREATE_TIMEOUT = int(os.environ.get("VDI_CREATE_TIMEOUT_SEC", "600"))
DESKTOP_USER = os.environ.get("VDI_DESKTOP_USER", "abc")
DESKTOP_PASSWORD = os.environ.get("VDI_DESKTOP_PASSWORD", "abc")

# 선택 가능한 OS 목록 (실제 환경에서는 Glance 이미지 조회로 대체)
IMAGES = {
    "ubuntu-xfce": {"name": "Ubuntu XFCE", "image": "lscr.io/linuxserver/rdesktop:ubuntu-xfce"},
    "ubuntu-mate": {"name": "Ubuntu MATE", "image": "lscr.io/linuxserver/rdesktop:ubuntu-mate"},
    "ubuntu-icewm": {"name": "Ubuntu IceWM (경량)", "image": "lscr.io/linuxserver/rdesktop:ubuntu-icewm"},
}

LABEL_APP = "vdi-desktop"

desktops_created = Counter("vdi_desktops_created_total", "Desktop create requests", ["os"])
desktops_deleted = Counter("vdi_desktops_deleted_total", "Desktop delete requests")
desktops_failed = Counter("vdi_desktops_failed_total", "Desktops that timed out and were rolled back")
desktops_current = Gauge("vdi_desktops", "Desktops by status", ["status"])


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
        ready = any(c.type == "Ready" and c.status == "True" for c in (pod.status.conditions or []))
        age = (datetime.now(timezone.utc) - created).total_seconds()
        if pod.metadata.deletion_timestamp:
            status = "DELETING"
        elif ready:
            status = "READY"
        elif pod.status.phase == "Failed" or age > CREATE_TIMEOUT:
            status = "ERROR"
        else:
            status = "CREATING"
        return {
            "id": labels["vdi/id"],
            "owner": labels["vdi/owner"],
            "os": labels["vdi/os"],
            "os_name": IMAGES.get(labels["vdi/os"], {}).get("name", labels["vdi/os"]),
            "status": status,
            "node": pod.spec.node_name,
            "created_at": created.isoformat(),
        }


backend = KubernetesDesktopBackend()

# ---------------------------------------------------------------- guacamole


def guacamole_data(username, desktop):
    """Encrypted guacamole-auth-json payload (HMAC-SHA256 sign + AES-128-CBC)."""
    payload = {
        "username": username,
        "expires": int((time.time() + 300) * 1000),
        "connections": {
            f"{desktop['os_name']} ({desktop['id']})": {
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
    token = authorization.removeprefix("Bearer ").strip()
    username, _, sig = token.rpartition(".")
    if not username or not hmac.compare_digest(sig, issue_token(username).rpartition(".")[2]):
        raise HTTPException(401, "login required")
    return username


# ---------------------------------------------------------------- api

app = FastAPI(title="VDI mock portal")


class LoginRequest(BaseModel):
    username: str


class CreateRequest(BaseModel):
    os: str


@app.post("/api/auth/login")
def login(req: LoginRequest):
    username = req.username.strip().lower()
    if not username.isalnum() or len(username) > 20:
        raise HTTPException(400, "username must be 1-20 alphanumeric characters")
    return {"token": issue_token(username), "username": username}


@app.get("/api/images")
def images():
    return [{"id": k, "name": v["name"]} for k, v in IMAGES.items()]


@app.post("/api/desktops", status_code=202)
def create_desktop(req: CreateRequest, user: str = Depends(current_user)):
    if req.os not in IMAGES:
        raise HTTPException(400, "unknown os")
    active = [d for d in backend.list(user) if d["status"] != "DELETING"]
    if len(active) >= QUOTA:
        raise HTTPException(409, f"quota exceeded ({QUOTA} desktops per user)")
    desktop_id = secrets.token_hex(3)
    backend.create(desktop_id, user, req.os)
    desktops_created.labels(req.os).inc()
    return {"id": desktop_id, "status": "CREATING"}


@app.get("/api/desktops")
def list_desktops(user: str = Depends(current_user)):
    result = backend.list(user)
    for d in result:
        if d["status"] == "ERROR":  # 타임아웃 → 롤백
            desktops_failed.inc()
            backend.delete(d["id"])
    return result


def _owned(desktop_id, user):
    d = backend.status(desktop_id)
    if not d or d["owner"] != user:
        raise HTTPException(404, "not found")
    return d


@app.get("/api/desktops/{desktop_id}")
def get_desktop(desktop_id: str, user: str = Depends(current_user)):
    return _owned(desktop_id, user)


@app.post("/api/desktops/{desktop_id}/connect")
def connect(desktop_id: str, user: str = Depends(current_user)):
    d = _owned(desktop_id, user)
    if d["status"] != "READY":
        raise HTTPException(409, f"desktop is {d['status']}")
    data = urllib.parse.quote(guacamole_data(user, d), safe="")
    return {"url": f"{GUAC_URL}?data={data}"}


@app.delete("/api/desktops/{desktop_id}", status_code=202)
def delete_desktop(desktop_id: str, user: str = Depends(current_user)):
    _owned(desktop_id, user)
    backend.delete(desktop_id)
    desktops_deleted.inc()
    return {"id": desktop_id, "status": "DELETING"}


@app.get("/metrics")
def metrics():
    counts = {}
    for d in backend.list():
        counts[d["status"]] = counts.get(d["status"], 0) + 1
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
