import React, { useEffect, useState, useCallback } from "react";
import { X, Search, ShieldCheck, ShieldOff, Crown, Users as UsersIcon, Activity, Loader2 } from "lucide-react";
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
            <span className="shrink-0 rounded-md bg-amber-500/10 text-amber-300 border border-amber-500/20 px-1.5 py-[1px] text-[9.5px] uppercase tracking-wider font-mono">premium</span>
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
              title={`Выдать премиум: ${d.label}`}
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
              <ShieldOff className="h-3 w-3" /> Снять премиум
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
      toast.success(`Премиум выдан: @${user.username || user.tg_id} (${days ? days + " дн." : "бессрочно"})`);
      await Promise.all([loadUsers(query), loadStats()]);
    } catch (e) {
      toast.error(e.message || "Не удалось выдать премиум");
    } finally {
      setBusyId(null);
    }
  };

  const onRevoke = async (user) => {
    setBusyId(user.tg_id);
    try {
      await api.adminRevokePremium({ tg_id: user.tg_id });
      toast.success(`Премиум снят: @${user.username || user.tg_id}`);
      await Promise.all([loadUsers(query), loadStats()]);
    } catch (e) {
      toast.error(e.message || "Не удалось снять премиум");
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
              <p className="text-[11px] text-white/40">Управление доступом и премиумом</p>
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
          <StatCard icon={Crown} label="Премиум" value={stats?.premium_users} />
          <StatCard icon={Activity} label="Активные 24ч" value={stats?.active_24h} hint={`7д: ${stats?.active_7d ?? "—"}`} />
          <StatCard
            icon={ShieldCheck}
            label="Ключи Gemini"
            value={stats ? `${stats.gemini_keys_healthy}/${stats.gemini_keys_total}` : null}
            hint="здоровые сейчас"
          />
        </div>

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
