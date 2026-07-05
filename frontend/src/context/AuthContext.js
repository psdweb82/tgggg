import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import { api, getToken, setToken, clearToken } from "@/lib/api";

const AuthContext = createContext(null);
export const useAuth = () => useContext(AuthContext);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
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
      // 2) Telegram Mini App auto-login (opened inside Telegram)
      const initData = window.Telegram?.WebApp?.initData;
      if (initData) {
        try {
          const { access_token, user: u } = await api.telegramWebApp(initData);
          setToken(access_token);
          setUser(u);
        } catch (e) {
          // fall through to login screen
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
