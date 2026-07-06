import React, { useEffect, useState, useCallback } from "react";
import { X, Search, ShieldCheck, ShieldOff, Crown, Users as UsersIcon, Activity, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";

const DURATIONS = [
  { label: "7 дней", days: 7 },
  { label: "30 дней", days: 30 },
  { label: "90 дней", days: 90 },
  { label: "Навсегда", days: null },
];

function StatCard({ icon: Icon, label, value, hint }) {
  return (
    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3">
      <div className="flex items-center gap-2 text-white/50 text-[11px] uppercase tracking-wider font-mono">
        <Icon className="h-3.5 w-3.5" strokeWidth={1.75} />
        {label}
      </div>
      <div className="mt-1.5 text-xl font-medium text-white tabular-nums">{value ?? "—"}</div>
      {hint ? <div className="text-[11px] text-white/35 mt-0.5">{hint}</div> : null}
    </div>
  );
}

const KEY_STATUS_STYLE = {
  healthy: "bg-emerald-500/10 text-emerald-300 border-emerald-500/25",
  cooldown: "bg-sky-500/10 text-sky-300 border-sky-500/25",
  daily_limit: "bg-amber-500/10 text-amber-300 border-amber-500/25",
  invalid: "bg-red-500/10 text-red-300 border-red-500/25",
  forbidden: "bg-red-500/10 text-red-300 border-red-500/25",
  broken: "bg-orange-500/10 text-orange-300 border-orange-500/25",
};

function KeyChip({ label, count, cls }) {
  return (
    <div className={`rounded-lg border px-2.5 py-1.5 flex items-center justify-between gap-2 ${cls}`}>
      <span className="text-[10.5px] uppercase tracking-wider font-mono">{label}</span>
      <span className="text-sm font-semibold tabular-nums">{count ?? "—"}</span>
    </div>
  );
}

function KeysPanel({ keys, onRefresh }) {
  const [open, setOpen] = useState(false);
  if (!keys) return null;
  const m = keys.metrics || {};
  const fmtNext = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }) +
      " " + d.toLocaleDateString("ru-RU", { day: "2-digit", month: "short" });
  };
  return (
    <div data-testid="keys-panel" className="px-5 pt-4">
      <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
        <div className="flex items-center justify-between mb-2.5">
          <p className="text-[11px] uppercase tracking-wider text-white/50 font-mono">Состояние API-ключей ({keys.total})</p>
          <div className="flex items-center gap-3">
            <button
              data-testid="refresh-key-stats"
              onClick={() => onRefresh?.()}
              className="inline-flex items-center gap-1.5 text-[11px] text-white/60 hover:text-white transition-colors rounded-md border border-white/10 hover:border-white/25 px-2 py-1"
            >
              <RefreshCw className="h-3 w-3" strokeWidth={2} /> Refresh key stats
            </button>
            <button
              data-testid="toggle-keys-detail"
              onClick={() => setOpen((v) => !v)}
              className="text-[11px] text-white/50 hover:text-white transition-colors"
            >
              {open ? "Скрыть детали" : "Показать детали"}
            </button>
          </div>
        </div>
        <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
          <KeyChip label="Рабочих" count={keys.healthy} cls={KEY_STATUS_STYLE.healthy} />
          <KeyChip label="Cooldown" count={keys.cooldown} cls={KEY_STATUS_STYLE.cooldown} />
          <KeyChip label="Daily" count={keys.daily_limit} cls={KEY_STATUS_STYLE.daily_limit} />
          <KeyChip label="Invalid" count={keys.invalid} cls={KEY_STATUS_STYLE.invalid} />
          <KeyChip label="Forbidden" count={keys.forbidden} cls={KEY_STATUS_STYLE.forbidden} />
          <KeyChip label="Broken" count={keys.broken} cls={KEY_STATUS_STYLE.broken} />
        </div>

        <div data-testid="keys-metrics" className="mt-3 grid grid-cols-2 sm:grid-cols-6 gap-2">
          <Metric label="Всего запросов" value={m.total_requests} />
          <Metric label="Ср. ответ" value={m.avg_response_ms != null ? `${m.avg_response_ms} мс` : "—"} />
          <Metric label="Переключений" value={m.total_switches} />
          <Metric label="429" value={m.total_429} />
          <Metric label="Daily Limit" value={m.total_daily_limit} />
          <Metric label="503" value={m.total_503} />
        </div>

        {open && (
          <div data-testid="keys-detail-list" className="mt-3 max-h-60 overflow-y-auto rounded-lg border border-white/[0.05]">
            <table className="w-full text-[11px]">
              <thead className="text-white/40 font-mono uppercase tracking-wider sticky top-0 bg-[#0a0a0b]">
                <tr className="text-left">
                  <th className="px-2 py-1.5">#</th>
                  <th className="px-2 py-1.5">Статус</th>
                  <th className="px-2 py-1.5 text-right">OK</th>
                  <th className="px-2 py-1.5 text-right">Err</th>
                  <th className="px-2 py-1.5 text-right">Запросов</th>
                  <th className="px-2 py-1.5">Активация</th>
                  <th className="px-2 py-1.5">Причина</th>
                </tr>
              </thead>
              <tbody>
                {keys.keys?.map((k) => (
                  <tr key={k.idx} data-testid={`key-row-${k.idx}`} className="border-t border-white/[0.04] text-white/70">
                    <td className="px-2 py-1.5 font-mono">{k.idx}</td>
                    <td className="px-2 py-1.5">
                      <span className={`rounded border px-1.5 py-[1px] text-[9.5px] uppercase tracking-wider font-mono ${KEY_STATUS_STYLE[k.status] || "bg-white/5 text-white/60 border-white/10"}`}>
                        {k.status_label}
                      </span>
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-emerald-300/80">{k.success}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-red-300/70">{k.errors}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{k.served}</td>
                    <td className="px-2 py-1.5 font-mono text-white/50">{k.next_activation_at ? fmtNext(k.next_activation_at) : "—"}</td>
                    <td className="px-2 py-1.5 text-white/45 truncate max-w-[140px]" title={k.last_reason}>{k.last_reason || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function Metric({ label, value }) {
  return (
    <div className="rounded-lg border border-white/[0.05] bg-white/[0.015] px-2.5 py-1.5">
      <div className="text-[9.5px] uppercase tracking-wider text-white/40 font-mono truncate">{label}</div>
      <div className="text-sm font-semibold text-white tabular-nums mt-0.5">{value ?? "—"}</div>
    </div>
  );
}

function UserRow({ u, onGrant, onRevoke, busyId }) {
  const displayName = [u.first_name, u.last_name].filter(Boolean).join(" ") || "Без имени";
  const handle = u.username ? `@${u.username}` : `ID ${u.tg_id}`;
  const isBusy = busyId === u.tg_id;
  const untilLabel = u.premium_until
    ? `до ${new Date(u.premium_until).toLocaleDateString("ru-RU", { day: "2-digit", month: "short", year: "numeric" })}`
    : "бессрочно";

  return (
    <div
      data-testid={`admin-user-row-${u.tg_id}`}
      className="group flex items-center gap-3 rounded-xl border border-white/[0.05] bg-white/[0.015] hover:bg-white/[0.03] px-3 py-2.5 transition-colors"
    >
      <div className="h-9 w-9 shrink-0 rounded-lg bg-gradient-to-br from-white/10 to-white/[0.03] ring-1 ring-white/[0.06] flex items-center justify-center overflow-hidden">
        {u.photo_url ? (
          <img src={u.photo_url} alt="" className="h-full w-full object-cover" />
        ) : (
          <span className="text-white/70 text-sm">{(u.first_name?.[0] || "?").toUpperCase()}</span>
        )}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <p className="truncate text-sm text-white">{displayName}</p>
          {u.is_admin && (
            <span className="shrink-0 rounded-md bg-violet-500/10 text-violet-300 border border-violet-500/20 px-1.5 py-[1px] text-[9.5px] uppercase tracking-wider font-mono">admin</span>
          )}
          {u.is_premium && !u.is_admin && (
            <span className="shrink-0 rounded-md bg-amber-500/10 text-amber-300 border border-amber-500/20 px-1.5 py-[1px] text-[9.5px] uppercase tracking-wider font-mono">Luxury</span>
          )}
        </div>
        <p className="text-[11px] text-white/40 truncate font-mono">
          {handle}
          {u.is_premium && !u.is_admin ? ` · ${untilLabel}` : ""}
        </p>
      </div>

      {u.is_admin ? (
        <span className="text-[11px] text-white/35 pr-2">не редактируется</span>
      ) : (
        <div className="flex items-center gap-1.5 opacity-70 group-hover:opacity-100 transition-opacity">
          {!u.is_premium && DURATIONS.map((d) => (
            <button
              key={d.label}
              data-testid={`admin-grant-${d.days ?? "forever"}-${u.tg_id}`}
              disabled={isBusy}
              onClick={() => onGrant(u, d.days)}
              className="rounded-md border border-white/[0.08] bg-white/[0.03] hover:bg-white/[0.08] hover:border-white/20 text-white/80 hover:text-white text-[11px] px-2 py-1 font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              title={`Выдать Luxury: ${d.label}`}
            >
              {d.label}
            </button>
          ))}
          {u.is_premium && (
            <button
              data-testid={`admin-revoke-${u.tg_id}`}
              disabled={isBusy}
              onClick={() => onRevoke(u)}
              className="rounded-md border border-red-500/25 bg-red-500/10 hover:bg-red-500/20 text-red-300 hover:text-red-200 text-[11px] px-2.5 py-1 font-medium transition-colors flex items-center gap-1 disabled:opacity-40"
            >
              <ShieldOff className="h-3 w-3" /> Снять Luxury
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default function AdminPanel({ open, onClose }) {
  const [stats, setStats] = useState(null);
  const [query, setQuery] = useState("");
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState(null);

  const loadStats = useCallback(async () => {
    try {
      const s = await api.adminStats();
      setStats(s);
    } catch (e) {
      toast.error(e.message || "Не удалось загрузить статистику");
    }
  }, []);

  const loadUsers = useCallback(async (q) => {
    setLoading(true);
    try {
      const r = await api.adminSearchUsers(q || "", 40);
      setUsers(r.users || []);
    } catch (e) {
      toast.error(e.message || "Не удалось загрузить юзеров");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    loadStats();
    loadUsers("");
    const id = setInterval(loadStats, 5000);  // живое состояние ключей
    return () => clearInterval(id);
  }, [open, loadStats, loadUsers]);

  useEffect(() => {
    if (!open) return;
    const t = setTimeout(() => loadUsers(query), 300);
    return () => clearTimeout(t);
  }, [query, open, loadUsers]);

  const onGrant = async (user, days) => {
    setBusyId(user.tg_id);
    try {
      await api.adminGrantPremium({ tg_id: user.tg_id, days });
      toast.success(`Luxury выдан: @${user.username || user.tg_id} (${days ? days + " дн." : "бессрочно"})`);
      await Promise.all([loadUsers(query), loadStats()]);
    } catch (e) {
      toast.error(e.message || "Не удалось выдать Luxury");
    } finally {
      setBusyId(null);
    }
  };

  const onRevoke = async (user) => {
    setBusyId(user.tg_id);
    try {
      await api.adminRevokePremium({ tg_id: user.tg_id });
      toast.success(`Luxury снят: @${user.username || user.tg_id}`);
      await Promise.all([loadUsers(query), loadStats()]);
    } catch (e) {
      toast.error(e.message || "Не удалось снять Luxury");
    } finally {
      setBusyId(null);
    }
  };

  if (!open) return null;

  return (
    <div
      data-testid="admin-panel-backdrop"
      className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        data-testid="admin-panel"
        className="w-full max-w-3xl max-h-[88vh] rounded-2xl bg-[#0a0a0b] border border-white/[0.08] shadow-[0_20px_80px_rgba(0,0,0,0.6)] overflow-hidden flex flex-col"
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/[0.06]">
          <div className="flex items-center gap-2.5">
            <div className="h-8 w-8 rounded-xl bg-gradient-to-br from-violet-500/20 to-violet-500/5 border border-violet-500/20 flex items-center justify-center">
              <ShieldCheck className="h-4 w-4 text-violet-300" strokeWidth={1.75} />
            </div>
            <div>
              <h2 className="text-white text-[15px] font-medium">Админ-панель</h2>
              <p className="text-[11px] text-white/40">Управление доступом и Luxury</p>
            </div>
          </div>
          <button
            data-testid="admin-panel-close"
            onClick={onClose}
            className="h-8 w-8 rounded-lg text-white/50 hover:text-white hover:bg-white/[0.06] flex items-center justify-center transition-colors"
            aria-label="Закрыть"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5 px-5 pt-4">
          <StatCard icon={UsersIcon} label="Всего юзеров" value={stats?.total_users} />
          <StatCard icon={Crown} label="Luxury" value={stats?.premium_users} />
          <StatCard icon={Activity} label="Активные 24ч" value={stats?.active_24h} hint={`7д: ${stats?.active_7d ?? "—"}`} />
          <StatCard
            icon={ShieldCheck}
            label="Ключи Gemini"
            value={stats ? `${stats.gemini_keys_healthy}/${stats.gemini_keys_total}` : null}
            hint="рабочих сейчас"
          />
        </div>

        <KeysPanel keys={stats?.gemini_keys} onRefresh={loadStats} />

        <div className="px-5 pt-4 pb-2">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-white/35" strokeWidth={1.75} />
            <input
              data-testid="admin-search-input"
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Поиск по @username, tg_id или имени…"
              className="w-full rounded-xl bg-white/[0.03] border border-white/[0.08] focus:border-white/25 outline-none pl-10 pr-4 py-2.5 text-sm text-white placeholder:text-white/30 transition-colors"
            />
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-5 pb-5 space-y-1.5">
          {loading && (
            <div className="flex items-center justify-center py-10 text-white/40 gap-2">
              <Loader2 className="h-4 w-4 animate-spin" /> Загрузка…
            </div>
          )}
          {!loading && users.length === 0 && (
            <div className="text-center py-10 text-white/35 text-sm">
              {query ? "Никого не нашлось." : "Юзеров пока нет."}
            </div>
          )}
          {!loading && users.map((u) => (
            <UserRow key={u.tg_id} u={u} onGrant={onGrant} onRevoke={onRevoke} busyId={busyId} />
          ))}
        </div>
      </div>
    </div>
  );
}
