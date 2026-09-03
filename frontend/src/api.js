const LEAGUE_KEY = "cfbsicko_league_id";

export function getLeagueId() {
  const raw = localStorage.getItem(LEAGUE_KEY);
  const n = raw ? Number(raw) : 0;
  return Number.isFinite(n) && n > 0 ? n : null;
}

export function setLeagueId(id) {
  if (id) localStorage.setItem(LEAGUE_KEY, String(id));
  else localStorage.removeItem(LEAGUE_KEY);
}

export async function api(path, { method = "GET", token, body } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  const leagueId = getLeagueId();
  if (leagueId) headers["X-League-Id"] = String(leagueId);
  const res = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text };
    }
  }
  if (!res.ok) {
    const err = new Error(data?.detail || res.statusText);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

export function pickLabel(pick, game) {
  const g = game || pick;
  if (pick.market === "total") {
    return `${g.away}/${g.home} ${pick.side === "over" ? "Over" : "Under"} ${g.total}`;
  }
  if (pick.side === "home") {
    const n = g.spread_home;
    return `${g.home} ${n > 0 ? "+" : ""}${n}`;
  }
  const n = -g.spread_home;
  return `${g.away} ${n > 0 ? "+" : ""}${n}`;
}
