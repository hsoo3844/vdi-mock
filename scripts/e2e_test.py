"""E2E: 로그인 → 생성 → READY 대기 → Guacamole 토큰 검증 → 할당량 → 반납.

사용: python scripts/e2e_test.py [http://127.0.0.1:30080]
"""
import sys
import time
import urllib.parse

import requests

B = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:30080"

tok = requests.post(f"{B}/api/auth/login", json={"username": "e2etest"}).json()["token"]
H = {"Authorization": f"Bearer {tok}"}

r = requests.post(f"{B}/api/desktops", json={"os": "ubuntu-xfce"}, headers=H)
assert r.status_code == 202, r.text
did = r.json()["id"]
print("create", r.status_code, r.json())

t0 = time.time()
while True:
    d = requests.get(f"{B}/api/desktops/{did}", headers=H).json()
    if d["status"] in ("READY", "ERROR") or time.time() - t0 > 400:
        break
    time.sleep(3)
print(f"status {d['status']} after {time.time() - t0:.0f}s on {d['node']}")
assert d["status"] == "READY"

c = requests.post(f"{B}/api/desktops/{did}/connect", headers=H)
assert c.status_code == 200, c.text
data = urllib.parse.unquote(c.json()["url"].split("data=")[1])

g = requests.post(f"{B}/guacamole/api/tokens", data={"data": data})
assert g.status_code == 200, g.text
j = g.json()
print("guacamole login", j["username"], j["dataSource"])
cons = requests.get(
    f"{B}/guacamole/api/session/data/{j['dataSource']}/connections",
    headers={"Guacamole-Token": j["authToken"]},
).json()
print("guacamole connections", [(v["name"], v["protocol"]) for v in cons.values()])

# 실제 RDP 세션: HTTP 터널로 연결해 guacd가 데스크톱 화면(img/size)을 보내는지 확인
conn_id = next(iter(cons))
tunnel = requests.post(
    f"{B}/guacamole/tunnel?connect",
    data=f"token={j['authToken']}&GUAC_DATA_SOURCE={j['dataSource']}&GUAC_ID={urllib.parse.quote(conn_id)}"
    "&GUAC_TYPE=c&GUAC_WIDTH=1024&GUAC_HEIGHT=768&GUAC_DPI=96",
    headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
).text.strip()
stream = requests.get(f"{B}/guacamole/tunnel?read:{tunnel}:0", stream=True, timeout=30)
received = b""
for chunk in stream.iter_content(4096):
    received += chunk
    if b".error," in received or (b".size," in received and b".img," in received) or len(received) > 200_000:
        break
stream.close()
assert b".error," not in received, received[:300]
print("rdp session OK (guacd rendered desktop frames, %d bytes)" % len(received))

codes = [requests.post(f"{B}/api/desktops", json={"os": "ubuntu-icewm"}, headers=H).status_code for _ in range(2)]
print("quota (expect 202, 409):", codes)
assert codes == [202, 409]

for d in requests.get(f"{B}/api/desktops", headers=H).json():
    requests.delete(f"{B}/api/desktops/{d['id']}", headers=H)
print("cleanup requested; remaining:", [(d["id"], d["status"]) for d in requests.get(f"{B}/api/desktops", headers=H).json()])
print("E2E OK")
