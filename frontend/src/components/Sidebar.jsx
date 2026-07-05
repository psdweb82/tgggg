import React from "react";
import { motion } from "framer-motion";
import { Plus, MessageSquare, Trash2, LogOut, Sparkles, Send, Lock } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import {
  Avatar, AvatarFallback, AvatarImage,
} from "@/components/ui/avatar";

function initials(name = "") {
  return name.trim().slice(0, 1).toUpperCase() || "U";
}

export default function Sidebar({ conversations, activeId, onSelect, onNew, onDelete }) {
  const { user, logout } = useAuth();

  return (
    <div data-testid="history-sidebar" className="flex h-full flex-col bg-[#050506] border-r border-white/[0.06]">
      <div className="p-4">
        <div className="flex items-center gap-2.5 px-2 mb-5">
          <div className="relative h-8 w-8 rounded-xl bg-gradient-to-br from-white to-white/70 text-black flex items-center justify-center shadow-[0_4px_20px_rgba(255,255,255,0.1)]">
            <Sparkles className="h-4 w-4" strokeWidth={1.75} />
          </div>
          <div className="flex flex-col leading-none">
            <span className="font-display text-[15px] font-medium tracking-tight text-white">AI Workspace</span>
          </div>
        </div>
        <button
          data-testid="new-chat-button"
          onClick={onNew}
          className="group w-full flex items-center gap-2 rounded-xl bg-white text-black px-4 py-2.5 text-sm font-medium hover:bg-white/90 transition-all hover:-translate-y-[1px] active:translate-y-0 shadow-[0_4px_20px_rgba(255,255,255,0.08)]"
        >
          <Plus className="h-4 w-4 transition-transform group-hover:rotate-90 duration-300" /> Новый чат
        </button>
      </div>

      <div className="px-4 pb-1.5">
        <p className="text-[10px] uppercase tracking-[0.14em] text-white/35 font-mono">История</p>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-2">
        {conversations.length === 0 ? (
          <p className="px-3 py-6 text-xs text-white/35 text-center">Пока нет чатов.<br />Начните новую беседу.</p>
        ) : (
          <div className="space-y-0.5">
            {conversations.map((c) => {
              const sizeMb = (c.size_bytes || 0) / (1024 * 1024);
              const showSize = sizeMb >= 1;
              return (
                <motion.div
                  key={c.id}
                  layout
                  data-testid="conversation-item"
                  onClick={() => onSelect(c.id)}
                  className={`group flex items-center gap-2.5 rounded-lg px-3 py-2.5 cursor-pointer transition-all ${
                    activeId === c.id
                      ? "bg-white/[0.08] shadow-[inset_0_0_0_1px_rgba(255,255,255,0.06)]"
                      : "hover:bg-white/[0.04]"
                  }`}
                >
                  {c.locked ? (
                    <Lock className="h-4 w-4 shrink-0 text-amber-400" strokeWidth={1.75} />
                  ) : (
                    <MessageSquare className={`h-4 w-4 shrink-0 ${activeId === c.id ? "text-white" : "text-white/40"}`} strokeWidth={1.5} />
                  )}
                  <div className="flex-1 min-w-0">
                    <p className={`truncate text-sm ${activeId === c.id ? "text-white" : "text-white/75"}`}>{c.title}</p>
                    {(showSize || c.locked) && (
                      <p className={`text-[10.5px] font-mono ${c.locked ? "text-amber-400/80" : "text-white/35"}`}>
                        {c.locked ? "заблокирован · " : ""}{sizeMb.toFixed(1)} МБ
                      </p>
                    )}
                  </div>
                  <button
                    data-testid="delete-conversation-button"
                    onClick={(e) => { e.stopPropagation(); onDelete(c.id); }}
                    aria-label="Удалить чат"
                    className="opacity-0 group-hover:opacity-100 text-white/40 hover:text-red-400 transition-all"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </motion.div>
              );
            })}
          </div>
        )}
      </div>

      <div className="border-t border-white/[0.06] px-3 py-3">
        <p className="px-2 text-[10px] uppercase tracking-[0.14em] text-white/35 mb-1.5 font-mono">Связь с нами</p>
        <div className="space-y-0.5">
          <a data-testid="creator-link-1" href="https://t.me/sabjenopromine" target="_blank" rel="noopener noreferrer"
             className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-white/75 hover:text-white hover:bg-white/[0.04] transition-colors">
            <Send className="h-4 w-4 text-white/40" strokeWidth={1.5} /> @sabjenopromine
          </a>
          <a data-testid="creator-link-2" href="https://t.me/luacristo" target="_blank" rel="noopener noreferrer"
             className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-white/75 hover:text-white hover:bg-white/[0.04] transition-colors">
            <Send className="h-4 w-4 text-white/40" strokeWidth={1.5} /> @luacristo
          </a>
        </div>
      </div>

      <div className="border-t border-white/[0.06] p-3">
        <div className="flex items-center gap-3 rounded-xl px-2 py-2">
          <Avatar className="h-8 w-8 ring-1 ring-white/10">
            <AvatarImage src={user?.photo_url} alt={user?.first_name} />
            <AvatarFallback className="bg-white/10 text-white text-xs">{initials(user?.first_name)}</AvatarFallback>
          </Avatar>
          <div className="flex-1 min-w-0">
            <p className="text-sm text-white truncate">{user?.first_name} {user?.last_name || ""}</p>
            <p className="text-[11px] text-white/40 truncate">
              {user?.username ? `@${user.username}` : `ID ${user?.tg_id}`}
            </p>
          </div>
          <button data-testid="logout-button" onClick={logout} aria-label="Выйти" className="h-8 w-8 rounded-lg text-white/50 hover:text-white hover:bg-white/[0.06] flex items-center justify-center transition-colors">
            <LogOut className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
