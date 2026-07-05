import React, { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { Sparkles, TerminalSquare, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";

function TelegramWidget({ botUsername, onAuth }) {
  const ref = useRef(null);
  useEffect(() => {
    if (!botUsername || !ref.current) return;
    window.onTelegramAuth = (u) => onAuth(u);
    const s = document.createElement("script");
    s.src = "https://telegram.org/js/telegram-widget.js?22";
    s.async = true;
    s.setAttribute("data-telegram-login", botUsername);
    s.setAttribute("data-size", "large");
    s.setAttribute("data-radius", "12");
    s.setAttribute("data-userpic", "true");
    s.setAttribute("data-request-access", "write");
    s.setAttribute("data-onauth", "onTelegramAuth(user)");
    ref.current.innerHTML = "";
    ref.current.appendChild(s);
  }, [botUsername, onAuth]);
  return <div ref={ref} data-testid="telegram-login-container" className="flex justify-center min-h-[48px]" />;
}

export default function Login() {
  const { loginWithToken } = useAuth();
  const [config, setConfig] = useState(null);
  const [mode, setMode] = useState("telegram");
  const [busy, setBusy] = useState(false);
  const [tgId, setTgId] = useState("");
  const [name, setName] = useState("");
  const [secret, setSecret] = useState("");

  useEffect(() => {
    api.getConfig().then(setConfig).catch(() => setConfig({ telegram_bot_username: "", dev_login_enabled: false }));
  }, []);

  const handleTelegram = async (u) => {
    setBusy(true);
    try {
      const { access_token, user } = await api.telegramLogin(u);
      loginWithToken(access_token, user);
      toast.success(`Добро пожаловать, ${user.first_name}!`);
    } catch (e) {
      toast.error(e.message || "Не удалось войти через Telegram");
    } finally {
      setBusy(false);
    }
  };

  const handleDev = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const { access_token, user } = await api.devLogin({
        tg_id: parseInt(tgId, 10),
        first_name: name || "Тестовый пользователь",
        dev_secret: secret,
      });
      loginWithToken(access_token, user);
      toast.success(`Вход выполнен: ${user.first_name}`);
    } catch (e) {
      toast.error(e.message || "Ошибка тестового входа");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="h-full w-full flex items-center justify-center bg-[#070708] px-5 relative overflow-hidden">
      {/* Ambient orbs */}
      <div className="pointer-events-none absolute -top-40 left-1/2 -translate-x-1/2 h-[520px] w-[520px] rounded-full bg-white/[0.05] blur-[120px]" />
      <div className="pointer-events-none absolute -bottom-40 -right-40 h-[420px] w-[420px] rounded-full bg-[#4a3ce6]/10 blur-[120px]" />
      <div className="pointer-events-none absolute inset-0 opacity-[0.03] bg-[url('data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22200%22 height=%22200%22><filter id=%22n%22><feTurbulence type=%22fractalNoise%22 baseFrequency=%220.9%22/></filter><rect width=%22100%25%22 height=%22100%25%22 filter=%22url(%23n)%22/></svg>')]" />

      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.55, ease: [0.22, 1, 0.36, 1] }}
        className="relative w-full max-w-md"
      >
        <div className="flex flex-col items-center text-center mb-9">
          <div className="relative h-14 w-14 rounded-2xl bg-gradient-to-br from-white to-white/70 text-black flex items-center justify-center mb-5 shadow-[0_20px_60px_rgba(255,255,255,0.15)]">
            <Sparkles className="h-7 w-7" strokeWidth={1.5} />
            <span className="absolute inset-0 rounded-2xl ring-1 ring-white/10" />
          </div>
          <h1 className="font-display text-4xl sm:text-5xl font-medium tracking-tight">
            AI <span className="bg-gradient-to-r from-white to-white/50 bg-clip-text text-transparent">Workspace</span>
          </h1>
          <p className="text-white/50 mt-3 text-sm max-w-xs leading-relaxed">
            Мульти-модельный чат на Gemini. Вход через Telegram — быстро и безопасно.
          </p>
        </div>

        <div className="relative rounded-3xl border border-white/10 bg-gradient-to-br from-[#131315] to-[#0D0D0F] p-7 shadow-[0_20px_60px_rgba(0,0,0,0.6),0_0_0_1px_rgba(255,255,255,0.02)_inset]">
          {mode === "telegram" ? (
            <div className="space-y-5">
              <div className="flex items-center justify-center">
                {config?.telegram_bot_username ? (
                  <TelegramWidget botUsername={config.telegram_bot_username} onAuth={handleTelegram} />
                ) : (
                  <p className="text-xs text-white/40">Загрузка виджета…</p>
                )}
              </div>
              <p className="text-[11px] leading-relaxed text-white/40 text-center">
                Виджет Telegram работает только на домене, привязанном к боту через
                <span className="text-white/70"> /setdomain</span> у @BotFather.
              </p>
            </div>
          ) : (
            <form onSubmit={handleDev} className="space-y-3" data-testid="dev-login-form">
              <Input data-testid="dev-tgid-input" placeholder="Telegram ID (например 777)" value={tgId}
                     onChange={(e) => setTgId(e.target.value)} inputMode="numeric" required
                     className="bg-[#0A0A0A] border-white/10 h-11 rounded-xl" />
              <Input data-testid="dev-name-input" placeholder="Имя" value={name}
                     onChange={(e) => setName(e.target.value)}
                     className="bg-[#0A0A0A] border-white/10 h-11 rounded-xl" />
              <Input data-testid="dev-secret-input" type="password" placeholder="Секрет тестового входа" value={secret}
                     onChange={(e) => setSecret(e.target.value)} required
                     className="bg-[#0A0A0A] border-white/10 h-11 rounded-xl" />
              <Button data-testid="dev-login-submit" type="submit" disabled={busy}
                      className="w-full h-11 bg-white text-black hover:bg-white/90 font-medium rounded-xl">
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Войти (тест)"}
              </Button>
            </form>
          )}

          {config?.dev_login_enabled && (
            <div className="mt-5 pt-4 border-t border-white/5 flex justify-center">
              <button
                data-testid="toggle-login-mode"
                onClick={() => setMode(mode === "telegram" ? "dev" : "telegram")}
                className="inline-flex items-center gap-1.5 text-xs text-white/40 hover:text-white transition-colors"
              >
                <TerminalSquare className="h-3.5 w-3.5" />
                {mode === "telegram" ? "Тестовый вход (без Telegram)" : "Вернуться к Telegram"}
              </button>
            </div>
          )}
        </div>

      </motion.div>
    </div>
  );
}
