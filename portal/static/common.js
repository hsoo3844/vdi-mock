// 포털·관리자 페이지 공통: API, 토스트, 모달, 아이콘, 포맷터
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const auth = {
  get token() { try { return localStorage.getItem('vdiToken'); } catch { return null; } },
  set(token, user) { try { localStorage.setItem('vdiToken', token); localStorage.setItem('vdiUser', user); } catch {} },
  clear() { try { localStorage.removeItem('vdiToken'); localStorage.removeItem('vdiUser'); } catch {} },
};

class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function api(method, path, body) {
  const headers = { 'Content-Type': 'application/json' };
  if (auth.token) headers.Authorization = 'Bearer ' + auth.token;
  const r = await fetch(path, { method, headers, body: body && JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = new ApiError(r.status, typeof data.detail === 'string' ? data.detail : r.statusText);
    if (r.status === 401) { auth.clear(); document.dispatchEvent(new CustomEvent('unauthorized')); }
    throw err;
  }
  return data;
}

const ERROR_TEXT = {
  'quota exceeded': '할당량을 모두 사용했어요. 기존 데스크톱을 반납한 뒤 다시 시도하세요.',
  'account disabled': '비활성화된 계정입니다. 관리자에게 문의하세요.',
  'username must be': '이름은 영문·숫자 1~20자로 입력하세요.',
  'user already exists': '이미 있는 사용자입니다.',
  'at least one active admin': '활성 관리자가 최소 1명은 있어야 해요.',
  'cannot delete yourself': '자기 자신은 삭제할 수 없어요.',
  'admin only': '관리자만 접근할 수 있어요.',
};
const humanError = e => {
  const msg = e?.message || String(e);
  const hit = Object.keys(ERROR_TEXT).find(k => msg.includes(k));
  return hit ? ERROR_TEXT[hit] : msg;
};

// ------------------------------------------------------------ icons (stroke, currentColor)
const I = (d, extra = '') => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${d}</svg>`;
const ICON = {
  logo: I('<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/><path d="m9 9 2 2 4-4"/>'),
  monitor: I('<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>'),
  plus: I('<path d="M12 5v14M5 12h14"/>'),
  play: I('<path d="M7 5v14l11-7z"/>'),
  trash: I('<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>'),
  logout: I('<path d="M15 4h4v16h-4M10 17l5-5-5-5M15 12H3"/>'),
  shield: I('<path d="M12 3 5 6v6c0 4 3 7.5 7 9 4-1.5 7-5 7-9V6z"/>'),
  users: I('<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.5 3.4-5.5 6.5-5.5s5.7 2 6.5 5.5M16 4.5a3.5 3.5 0 0 1 0 7M18 14.8c1.8.8 3 2.6 3.5 5.2"/>'),
  grid: I('<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>'),
  activity: I('<path d="M3 12h4l3-8 4 16 3-8h4"/>'),
  server: I('<rect x="3" y="4" width="18" height="7" rx="1.5"/><rect x="3" y="13" width="18" height="7" rx="1.5"/><path d="M7 7.5h.01M7 16.5h.01"/>'),
  check: I('<path d="m5 12 5 5L20 7"/>'),
  alert: I('<circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/>'),
  info: I('<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>'),
  search: I('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>'),
  x: I('<path d="M6 6l12 12M18 6 6 18"/>'),
  login: I('<path d="M9 4H5v16h4M14 17l5-5-5-5M19 12H9"/>'),
  cpu: I('<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>'),
  clock: I('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
  key: I('<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M16 7l3 3"/>'),
  userPlus: I('<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.5 3.4-5.5 6.5-5.5s5.7 2 6.5 5.5M19 8v6M16 11h6"/>'),
  arrow: I('<path d="M5 12h14M13 6l6 6-6 6"/>'),
  empty: I('<rect x="3" y="4" width="18" height="12" rx="2" stroke-dasharray="3 3"/><path d="M8 20h8M12 16v4"/>', 'stroke-width="1.4"'),
};

// 정적 마크업의 <span data-icon="name"></span> 자리에 아이콘을 채운다
const hydrateIcons = (root = document) => $$('[data-icon]', root).forEach(el => { el.innerHTML = ICON[el.dataset.icon] || ''; });

// OS 카드 비주얼 (브랜드 로고가 아닌 데스크톱 환경별 색 + 모니터 아이콘)
const OS_STYLE = {
  'ubuntu-xfce': { color: '#4f7cff', badge: 'XFCE', note: '가볍고 표준적인 데스크톱' },
  'ubuntu-mate': { color: '#16a34a', badge: 'MATE', note: '클래식한 GNOME 2 스타일' },
  'ubuntu-icewm': { color: '#0891b2', badge: 'IceWM', note: '최소 자원, 빠른 부팅' },
};
const osStyle = id => OS_STYLE[id] || { color: '#6366f1', badge: 'Linux', note: '' };
const osIcon = (id, size = 44) => {
  const s = osStyle(id);
  return `<span class="os-icon" style="--c:${s.color};width:${size}px;height:${size}px">${ICON.monitor}</span>`;
};

// ------------------------------------------------------------ formatters
const pad = n => String(n).padStart(2, '0');
function timeAgo(t) {
  if (!t) return '-';
  const s = Math.max(0, (Date.now() - new Date(t)) / 1000);
  if (s < 45) return '방금 전';
  if (s < 3600) return `${Math.round(s / 60)}분 전`;
  if (s < 86400) return `${Math.round(s / 3600)}시간 전`;
  return `${Math.round(s / 86400)}일 전`;
}
const elapsed = t => { const s = Math.max(0, Math.floor((Date.now() - new Date(t)) / 1000)); return `${pad(Math.floor(s / 60))}:${pad(s % 60)}`; };
const fullTime = t => t ? new Date(t).toLocaleString('ko-KR', { hour12: false }) : '-';

const STATUS_TEXT = { CREATING: '생성 중', READY: '준비됨', DELETING: '반납 중', ERROR: '오류' };
const statusPill = s => `<span class="pill ${s}"><span class="dot"></span>${STATUS_TEXT[s] || s}</span>`;

const AVATAR_COLORS = ['#4f46e5', '#7c3aed', '#db2777', '#ea580c', '#059669', '#0891b2', '#2563eb', '#9333ea'];
function avatar(name, size = 32) {
  let h = 0; for (const c of name) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return `<span class="avatar" style="background:${AVATAR_COLORS[h % AVATAR_COLORS.length]};width:${size}px;height:${size}px;font-size:${Math.round(size * .4)}px">${esc(name[0] || '?')}</span>`;
}

// ------------------------------------------------------------ toast
function toast(text, kind = 'ok') {
  let box = $('#toasts');
  if (!box) { box = document.createElement('div'); box.id = 'toasts'; document.body.append(box); }
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.setAttribute('role', 'status');
  el.innerHTML = (kind === 'err' ? ICON.alert : kind === 'info' ? ICON.info : ICON.check) + `<span>${esc(text)}</span>`;
  box.append(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 200); }, 3200);
}

// ------------------------------------------------------------ modal
// modal({ title, body, fields:[{id,label,type,options,value,min}], confirm, danger }) → Promise<values|null>
function modal({ title, body = '', fields = [], confirm = '확인', danger = false }) {
  return new Promise(resolve => {
    const wrap = document.createElement('div');
    wrap.className = 'modal-backdrop';
    const fieldHtml = fields.map(f => `<div class="field"><label for="m-${f.id}">${esc(f.label)}</label>${
      f.type === 'select'
        ? `<select class="select" id="m-${f.id}">${f.options.map(o => `<option value="${esc(o.value)}" ${o.value === f.value ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}</select>`
        : `<input class="input" id="m-${f.id}" type="${f.type || 'text'}" value="${esc(f.value ?? '')}" ${f.min != null ? `min="${f.min}"` : ''} placeholder="${esc(f.placeholder || '')}" autocomplete="off">`
    }</div>`).join('');
    wrap.innerHTML = `<form class="modal card" role="dialog" aria-modal="true" aria-label="${esc(title)}">
      <h3>${esc(title)}</h3>${body ? `<p>${body}</p>` : ''}
      ${fields.length ? `<div class="fields">${fieldHtml}</div>` : ''}
      <div class="actions"><button type="button" class="btn btn-ghost" data-cancel>취소</button>
      <button class="btn ${danger ? 'btn-danger' : 'btn-primary'}">${esc(confirm)}</button></div></form>`;
    const close = v => { wrap.remove(); document.removeEventListener('keydown', onKey); resolve(v); };
    const onKey = e => { if (e.key === 'Escape') close(null); };
    document.addEventListener('keydown', onKey);
    wrap.addEventListener('mousedown', e => { if (e.target === wrap) close(null); });
    $('[data-cancel]', wrap).onclick = () => close(null);
    wrap.firstElementChild.onsubmit = e => {
      e.preventDefault();
      close(Object.fromEntries(fields.map(f => [f.id, $(`#m-${f.id}`, wrap).value])));
    };
    document.body.append(wrap);
    ($('input, select', wrap) || $('.btn-primary, .btn-danger', wrap)).focus();
  });
}

// 버튼 로딩 상태를 감싸는 헬퍼
async function busy(btn, fn) {
  btn?.classList.add('loading'); if (btn) btn.disabled = true;
  try { return await fn(); } finally { btn?.classList.remove('loading'); if (btn) btn.disabled = false; }
}
