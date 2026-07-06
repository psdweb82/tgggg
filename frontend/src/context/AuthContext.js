import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import { api, getToken, setToken, clearToken } from "@/lib/api";

const AuthContext = createContext(null);
export const useAuth = () => useContext(AuthContext);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      // 0) Telegram Mini App: инициализируем SDK и пробуем авто-вход ПЕРВЫМ,
      //    чтобы вход внутри Telegram всегда срабатывал (в т.ч. после очистки БД).
      const tg = window.Telegram?.WebApp;
      if (tg) {
        try { tg.ready(); tg.expand(); } catch (e) { /* ignore */ }
        // SDK может заполнить initData на следующий тик — короткое ожидание.
        let initData = tg.initData;
        if (!initData) {
          await new Promise((r) => setTimeout(r, 150));
          initData = window.Telegram?.WebApp?.initData;
        }
        if (initData) {
          try {
            const { access_token, user: u } = await api.telegramWebApp(initData);
            setToken(access_token);
            setUser(u);
            setLoading(false);
            return;
          } catch (e) {
            // подпись не прошла / бэкенд недоступен — пробуем существующую сессию ниже
          }
        }
      }

      // 1) existing session
      if (getToken()) {
        try {
          const u = await api.me();
          setUser(u);
          setLoading(false);
          return;
        } catch (e) {
          clearToken();
        }
      }

      setLoading(false);
    })();
  }, []);

  const loginWithToken = useCallback((token, u) => {
    setToken(token);
    setUser(u);
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, loginWithToken, logout }}>
      {children}
    </AuthContext.Provider>
  );
}
