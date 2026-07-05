import React, { useEffect, useRef, useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Menu, Sparkles, Zap, Code2, Lightbulb, PenLine, Compass, Lock } from "lucide-react";
import { toast } from "sonner";
import { api, streamChat, uploadImage, imageUrl } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import Sidebar from "@/components/Sidebar";
import Composer, { COMPOSER_LIMITS } from "@/components/Composer";
import ModelSelector from "@/components/ModelSelector";
import MessageContent from "@/components/MessageContent";
import ErrorBlock from "@/components/ErrorBlock";
import GeneratedImage from "@/components/GeneratedImage";
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet";

const SUGGESTIONS = [
  { icon: Lightbulb, text: "Объясни квантовую запутанность простыми словами", tag: "Обучение" },
  { icon: Code2,     text: "Напиши функцию на Python для сортировки слиянием", tag: "Код" },
  { icon: PenLine,   text: "Помоги составить деловое письмо клиенту",           tag: "Текст" },
  { icon: Compass,   text: "Составь план поездки в Токио на 5 дней",           tag: "Идеи" },
];

const REVEAL_MIN_PER_FRAME = 2;
const REVEAL_CATCHUP = 8;
const REVEAL_FADE_TAIL = 24;

export default function Chat() {
  const { user } = useAuth();
  const [models, setModels] = useState([]);
  const [model, setModel] = useState("gemini-2.5-flash-lite");
  const [conversations, setConversations] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [activeConv, setActiveConv] = useState(null);        // {id,title,size_bytes,locked,...}
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [attachments, setAttachments] = useState([]);        // [{ file?, previewUrl, uploading, id?, mime?, error?, size }]
  const scrollRef = useRef(null);
  const stopRef = useRef(false);
  const controllerRef = useRef(null);
  const bufferRef = useRef("");
  const renderedLenRef = useRef(0);
  const aiIdRef = useRef(null);
  const lastSendRef = useRef(null);

  const activeLocked = !!activeConv?.locked;

  const scrollToBottom = useCallback((behavior = "smooth") => {
    requestAnimationFrame(() => {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior });
    });
  }, []);

  useEffect(() => {
    api.models().then((d) => setModels(d.models)).catch(() => {});
    refreshConversations();
  }, []);

  useEffect(() => { scrollToBottom(); }, [messages, scrollToBottom]);

  const refreshConversations = async () => {
    try {
      const d = await api.listConversations();
      setConversations(d.conversations);
    } catch { /* ignore */ }
  };

  const openConversation = async (id) => {
    setActiveId(id);
    setSheetOpen(false);
    setAttachments([]);
    try {
      const d = await api.getConversation(id);
      setActiveConv(d.conversation);
      // Backend returns images with `url` relative (`/api/images/...`) — normalize to absolute
      setMessages(d.messages.map((m, i) => ({
        id: `${id}-${i}`,
        role: m.role,
        content: m.content,
        images: (m.images || []).map((im) => ({ ...im, url: imageUrl(im.id) })),
        created_at: m.created_at,
      })));
      if (d.conversation?.model) setModel(d.conversation.model);
    } catch {
      toast.error("Не удалось загрузить чат");
    }
  };

  const newChat = () => {
    setActiveId(null);
    setActiveConv(null);
    setMessages([]);
    setInput("");
    setAttachments([]);
    setSheetOpen(false);
  };

  const deleteChat = async (id) => {
    try {
      await api.deleteConversation(id);
      setConversations((cs) => cs.filter((c) => c.id !== id));
      if (activeId === id) newChat();
      toast.success("Чат удалён — лимит на 5 чатов восстановлен");
    } catch {
      toast.error("Не удалось удалить");
    }
  };

  const handlePickFiles = async (files) => {
    const remaining = COMPOSER_LIMITS.MAX_IMAGES - attachments.length;
    if (remaining <= 0) { toast.error(`Максимум ${COMPOSER_LIMITS.MAX_IMAGES} изображения`); return; }
    const list = files.slice(0, remaining);
    const rejected = files.length - list.length;
    if (rejected > 0) toast(`Пропущено ${rejected}: превышен лимит в ${COMPOSER_LIMITS.MAX_IMAGES} изображения`);

    const drafts = list.map((f) => {
      const previewUrl = URL.createObjectURL(f);
      let error = null;
      if (!f.type.startsWith("image/")) error = "Не картинка";
      else if (f.size > COMPOSER_LIMITS.MAX_BYTES) error = `> 20 МБ`;
      return { file: f, previewUrl, uploading: !error, size: f.size, error };
    });
    setAttachments((prev) => [...prev, ...drafts]);

    // Upload valid ones in parallel
    drafts.forEach(async (d) => {
      if (d.error) return;
      try {
        const r = await uploadImage(d.file);
        setAttachments((prev) => prev.map((a) => a.previewUrl === d.previewUrl
          ? { ...a, uploading: false, id: r.id, mime: r.mime } : a));
      } catch (e) {
        setAttachments((prev) => prev.map((a) => a.previewUrl === d.previewUrl
          ? { ...a, uploading: false, error: e.message || "Ошибка" } : a));
      }
    });
  };

  const removeAttachment = (idx) => {
    setAttachments((prev) => {
      const a = prev[idx];
      if (a?.previewUrl?.startsWith("blob:")) URL.revokeObjectURL(a.previewUrl);
      return prev.filter((_, i) => i !== idx);
    });
  };

  const send = async (text, opts = {}) => {
    const content = (text ?? input).trim();
    const usableAttachments = attachments.filter((a) => a.id && !a.uploading && !a.error);
    if (!opts.retry && !content && usableAttachments.length === 0) return;
    if (streaming) return;
    if (activeLocked) { toast.error("Чат заблокирован. Удалите его, чтобы продолжить."); return; }

    const attachmentsToSend = opts.retry
      ? (lastSendRef.current?.attachments || [])
      : usableAttachments.map((a) => ({ id: a.id, mime: a.mime, url: imageUrl(a.id) }));
    const useModel = opts.retry ? (lastSendRef.current?.model || model) : model;
    if (!opts.retry) {
      setInput("");
      setAttachments([]);
      lastSendRef.current = { content, attachments: attachmentsToSend, model: useModel };
    }
    stopRef.current = false;

    const aiId = `a-${Date.now()}`;
    aiIdRef.current = aiId;
    bufferRef.current = "";
    renderedLenRef.current = 0;

    if (!opts.retry) {
      const userMsg = {
        id: `u-${Date.now()}`, role: "user", content,
        images: attachmentsToSend,
      };
      setMessages((m) => [...m, userMsg, {
        id: aiId, role: "assistant", content: "", images: [],
        error: null, streaming: true, streamingLen: 0,
      }]);
    } else {
      setMessages((m) => {
        const copy = [...m];
        for (let i = copy.length - 1; i >= 0; i--) {
          if (copy[i].role === "assistant") {
            copy[i] = { id: aiId, role: "assistant", content: "", images: [], error: null, streaming: true, streamingLen: 0 };
            return copy;
          }
        }
        return [...copy, { id: aiId, role: "assistant", content: "", images: [], error: null, streaming: true, streamingLen: 0 }];
      });
    }
    setStreaming(true);

    const controller = new AbortController();
    controllerRef.current = controller;

    let rafId = requestAnimationFrame(function tick() {
      const targetLen = bufferRef.current.length;
      const cur = renderedLenRef.current;
      if (cur < targetLen) {
        const gap = targetLen - cur;
        const step = Math.max(REVEAL_MIN_PER_FRAME, Math.ceil(gap / REVEAL_CATCHUP));
        const next = Math.min(targetLen, cur + step);
        renderedLenRef.current = next;
        const shown = bufferRef.current.slice(0, next);
        setMessages((m) => m.map((msg) =>
          msg.id === aiId ? { ...msg, content: shown, streamingLen: REVEAL_FADE_TAIL } : msg,
        ));
      }
      rafId = requestAnimationFrame(tick);
    });

    let convId = activeId;
    let hadError = false;
    await streamChat(
      {
        conversationId: activeId,
        model: useModel,
        content,
        imageIds: attachmentsToSend.map((a) => a.id),
        signal: controller.signal,
      },
      {
        onMeta: (evt) => { if (!convId) { convId = evt.conversation_id; setActiveId(evt.conversation_id); } },
        onDelta: (t) => { if (!stopRef.current) bufferRef.current += t; },
        onError: (msg) => {
          hadError = true;
          setMessages((mm) => mm.map((x) => x.id === aiId ? { ...x, error: msg || "Ошибка генерации" } : x));
        },
        onDone: () => {},
      }
    );

    cancelAnimationFrame(rafId);
    const finalText = bufferRef.current;
    renderedLenRef.current = finalText.length;
    setMessages((m) => m.map((msg) =>
      msg.id === aiId ? { ...msg, content: finalText, streaming: false, streamingLen: 0, stopped: stopRef.current && !hadError } : msg,
    ));
    setStreaming(false);
    controllerRef.current = null;
    if (!activeId && convId) setActiveId(convId);
    refreshConversations();
  };

  const stop = () => {
    stopRef.current = true;
    controllerRef.current?.abort();
    setStreaming(false);
  };

  const retryLast = () => {
    if (!lastSendRef.current) return;
    send(lastSendRef.current.content, { retry: true });
  };

  return (
    <div className="h-full w-full text-white flex overflow-hidden relative">
      <div className="pointer-events-none absolute inset-0 -z-10 bg-[#070708]">
        <div className="absolute top-[-20%] left-1/2 -translate-x-1/2 h-[540px] w-[540px] rounded-full bg-white/[0.04] blur-[120px]" />
        <div className="absolute bottom-[-30%] right-[-10%] h-[420px] w-[420px] rounded-full bg-[#4a3ce6]/[0.10] blur-[140px]" />
        <div className="absolute inset-0 opacity-[0.035] mix-blend-screen bg-[url('data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22200%22 height=%22200%22><filter id=%22n%22><feTurbulence type=%22fractalNoise%22 baseFrequency=%220.9%22/></filter><rect width=%22100%25%22 height=%22100%25%22 filter=%22url(%23n)%22/></svg>')]" />
      </div>

      <aside className="hidden md:block w-72 shrink-0 border-r border-white/5">
        <Sidebar conversations={conversations} activeId={activeId} onSelect={openConversation} onNew={newChat} onDelete={deleteChat} />
      </aside>

      <main className="flex-1 flex flex-col min-w-0 relative">
        <header className="fixed top-0 left-0 right-0 md:pl-72 z-20">
          <div className="border-b border-white/[0.06] bg-[#070708]/70 backdrop-blur-2xl">
            <div className="flex items-center gap-3 h-14 px-4 max-w-3xl mx-auto w-full">
              <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
                <SheetTrigger asChild>
                  <button data-testid="open-sidebar-button" aria-label="Меню" className="md:hidden h-9 w-9 rounded-lg text-white/80 hover:text-white hover:bg-white/5 flex items-center justify-center transition-colors">
                    <Menu className="h-5 w-5" />
                  </button>
                </SheetTrigger>
                <SheetContent side="left" className="p-0 w-80 border-white/5 bg-[#050505]">
                  <Sidebar conversations={conversations} activeId={activeId} onSelect={openConversation} onNew={newChat} onDelete={deleteChat} />
                </SheetContent>
              </Sheet>
              {models.length > 0 && <ModelSelector models={models} value={model} onChange={setModel} />}
              <div className="flex-1" />
              {activeLocked && (
                <span data-testid="header-lock" className="inline-flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-amber-300/80 font-mono">
                  <Lock className="h-3.5 w-3.5" /> ЗАБЛОКИРОВАН
                </span>
              )}
            </div>
          </div>
        </header>

        <div ref={scrollRef} className="flex-1 overflow-y-auto scroll-smooth" data-testid="chat-thread">
          <div className="max-w-3xl mx-auto w-full px-4 sm:px-6 md:px-8 pt-24 pb-44">
            {messages.length === 0 ? (
              <div className="flex flex-col items-center justify-center text-center pt-8 sm:pt-16">
                <motion.div
                  initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
                  className="relative h-16 w-16 rounded-2xl bg-gradient-to-br from-white to-white/70 text-black flex items-center justify-center mb-6 shadow-[0_20px_60px_rgba(255,255,255,0.15)]">
                  <Sparkles className="h-8 w-8" strokeWidth={1.5} />
                  <span className="absolute inset-0 rounded-2xl ring-1 ring-white/10" />
                </motion.div>
                <motion.h2 initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 }}
                  className="font-display text-3xl sm:text-4xl font-light tracking-tight">
                  Привет, <span className="bg-gradient-to-r from-white to-white/60 bg-clip-text text-transparent">{user?.first_name}</span>
                </motion.h2>
                <motion.p initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.12 }}
                  className="text-white/50 mt-2 text-sm">Чем могу помочь сегодня?</motion.p>

                <div className="grid sm:grid-cols-2 gap-2.5 mt-10 w-full max-w-xl">
                  {SUGGESTIONS.map((s, i) => {
                    const Icon = s.icon;
                    return (
                      <motion.button
                        key={i}
                        data-testid={`suggestion-${i}`}
                        initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.18 + i * 0.06, ease: [0.22, 1, 0.36, 1] }}
                        onClick={() => send(s.text)}
                        className="group relative overflow-hidden text-left rounded-2xl border border-white/[0.08] bg-gradient-to-br from-white/[0.03] to-white/[0.01] hover:from-white/[0.06] hover:to-white/[0.02] px-4 py-3.5 transition-all hover:-translate-y-[2px] hover:border-white/20"
                      >
                        <div className="flex items-start gap-3">
                          <div className="h-8 w-8 shrink-0 rounded-lg bg-white/[0.06] border border-white/10 flex items-center justify-center text-white/70 group-hover:text-white transition-colors">
                            <Icon className="h-4 w-4" strokeWidth={1.75} />
                          </div>
                          <div className="min-w-0">
                            <p className="text-[11px] uppercase tracking-wider text-white/40 font-mono">{s.tag}</p>
                            <p className="mt-0.5 text-sm text-white/85 leading-snug">{s.text}</p>
                          </div>
                        </div>
                      </motion.button>
                    );
                  })}
                </div>
              </div>
            ) : (
              <div className="flex flex-col gap-7 sm:gap-8">
                <AnimatePresence initial={false}>
                  {messages.map((m) => (
                    <motion.div
                      key={m.id}
                      initial={{ opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
                      data-testid={`message-${m.role}`}
                      className={`flex gap-3 ${m.role === "user" ? "justify-end" : "justify-start"}`}
                    >
                      {m.role === "assistant" && (
                        <div className="h-8 w-8 shrink-0 rounded-lg bg-gradient-to-br from-white to-white/70 text-black flex items-center justify-center mt-1 shadow-[0_4px_20px_rgba(255,255,255,0.08)]">
                          <Zap className="h-4 w-4" strokeWidth={2} />
                        </div>
                      )}
                      {m.role === "user" ? (
                        <div className="max-w-[85%] rounded-2xl rounded-tr-md bg-gradient-to-br from-white/[0.08] to-white/[0.04] border border-white/10 px-4 py-3 text-white backdrop-blur-sm">
                          {m.images && m.images.length > 0 && (
                            <div className="mb-2 flex flex-wrap gap-2">
                              {m.images.map((img, i) => (
                                <GeneratedImage key={img.id || i} src={img.url} mime={img.mime} />
                              ))}
                            </div>
                          )}
                          {m.content && <p className="whitespace-pre-wrap leading-relaxed text-[15px]">{m.content}</p>}
                        </div>
                      ) : (
                        <div className="flex-1 min-w-0 pt-0.5 space-y-3">
                          {(m.content || (m.images && m.images.length > 0)) && (
                            <MessageContent
                              content={m.content}
                              images={m.images}
                              streaming={m.streaming}
                              streamingLen={m.streamingLen || 0}
                            />
                          )}
                          {!m.content && !m.error && !m.stopped && m.streaming && (
                            <div className="flex items-center gap-1.5 py-1" data-testid="thinking-indicator">
                              <span className="thinking-dot" />
                              <span className="thinking-dot" style={{ animationDelay: "0.15s" }} />
                              <span className="thinking-dot" style={{ animationDelay: "0.3s" }} />
                            </div>
                          )}
                          {m.error && <ErrorBlock message={m.error} onRetry={retryLast} />}
                          {m.stopped && !m.error && <ErrorBlock variant="stop" message="Генерация ответа была прервана вами." />}
                        </div>
                      )}
                    </motion.div>
                  ))}
                </AnimatePresence>
              </div>
            )}
          </div>
        </div>

        <Composer
          value={input}
          onChange={setInput}
          onSend={() => send()}
          onStop={stop}
          streaming={streaming}
          disabled={false}
          attachments={attachments}
          onPickFiles={handlePickFiles}
          onRemoveAttachment={removeAttachment}
          locked={activeLocked}
          lockedHint={activeLocked
            ? `Чат «${activeConv?.title || ''}» занимает ~${((activeConv?.size_bytes || 0) / (1024*1024)).toFixed(1)} МБ (> 150 МБ). Скопируйте нужное и удалите его, чтобы разблокировать лимит на 5 чатов.`
            : ""}
        />
      </main>
    </div>
  );
}
