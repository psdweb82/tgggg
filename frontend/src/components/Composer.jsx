import React, { useRef, useEffect } from "react";
import { ArrowUp, Square, Paperclip, X, Loader2, Clock } from "lucide-react";

const MAX_IMAGES = 3;
const MAX_BYTES = 20 * 1024 * 1024;
const MAX_CHARS = 500;

export default function Composer({
  value, onChange, onSend, onStop, streaming, disabled,
  attachments,          // [{ id?, previewUrl, uploading, name, size, error }]
  onPickFiles,
  onRemoveAttachment,
  placeholder = "Спросите что угодно…",
  locked = false,       // heavy-chat: input disabled
  lockedHint = "",
  cooldownActive = false,   // почасовой лимит достигнут
  cooldownLabel = "",       // "мм:сс" до разблокировки
}) {
  const ref = useRef(null);
  const fileRef = useRef(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }, [value]);

  const blocked = locked || cooldownActive;
  const anyUploading = attachments.some((a) => a.uploading);
  const remaining = MAX_CHARS - value.length;
  const canSend = !disabled && !blocked && !anyUploading &&
    (value.trim() || attachments.some((a) => a.id && !a.error));

  const handleChange = (e) => {
    // жёсткий лимит 500 символов — превысить нельзя
    onChange(e.target.value.slice(0, MAX_CHARS));
  };

  const handleKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!streaming && canSend) onSend();
    }
  };

  const openPicker = () => {
    if (blocked || attachments.length >= MAX_IMAGES) return;
    fileRef.current?.click();
  };

  const pickFiles = (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    if (!files.length) return;
    onPickFiles?.(files);
  };

  return (
    <div className="fixed bottom-0 left-0 right-0 md:pl-72 z-30 pointer-events-none">
      <div className="mx-auto max-w-3xl px-3 sm:px-4 pb-3 sm:pb-4 pt-8 bg-gradient-to-t from-[#070708] via-[#070708]/95 to-transparent">
        {cooldownActive && (
          <div data-testid="chat-cooldown-banner" className="pointer-events-auto mb-2 rounded-2xl border border-sky-500/25 bg-gradient-to-br from-sky-500/[0.10] via-sky-500/[0.04] to-transparent px-4 py-2.5 text-[12.5px] text-sky-100/90 backdrop-blur-sm flex items-center gap-2.5">
            <Clock className="h-4 w-4 shrink-0 text-sky-300" strokeWidth={1.75} />
            <span>Лимит запросов исчерпан. Чат снова станет доступен через{" "}
              <span data-testid="cooldown-timer" className="font-mono font-semibold text-white tabular-nums">{cooldownLabel}</span>
            </span>
          </div>
        )}
        {locked && (
          <div data-testid="chat-locked-banner" className="pointer-events-auto mb-2 rounded-2xl border border-amber-500/25 bg-gradient-to-br from-amber-500/[0.08] via-amber-500/[0.04] to-transparent px-4 py-2.5 text-[12.5px] text-amber-100/90 backdrop-blur-sm">
            🔒 {lockedHint || "Этот чат превысил 150 МБ и заблокирован для новых сообщений. Скопируйте нужное и удалите его."}
          </div>
        )}
        <div className={`pointer-events-auto rounded-3xl border p-2 shadow-[0_20px_50px_rgba(0,0,0,0.6),0_0_0_1px_rgba(255,255,255,0.02)_inset] transition-all
          ${blocked ? "border-white/[0.06] bg-[#0F0F11]/60 opacity-60"
                   : "border-white/10 bg-[#0F0F11]/90 backdrop-blur-2xl focus-within:border-white/20 focus-within:shadow-[0_20px_60px_rgba(0,0,0,0.7),0_0_0_1px_rgba(255,255,255,0.04)_inset]"}`}>
          {attachments.length > 0 && (
            <div className="flex flex-wrap gap-2 p-1.5">
              {attachments.map((a, i) => (
                <div key={a.previewUrl + i} data-testid={`attachment-${i}`} className="relative">
                  <img src={a.previewUrl} alt="preview" className="h-16 w-16 rounded-xl object-cover border border-white/10" />
                  {a.uploading && (
                    <div className="absolute inset-0 rounded-xl bg-black/60 flex items-center justify-center backdrop-blur-sm">
                      <Loader2 className="h-4 w-4 animate-spin text-white" />
                    </div>
                  )}
                  {a.error && (
                    <div className="absolute inset-0 rounded-xl bg-red-500/70 flex items-center justify-center text-[10px] text-white text-center leading-tight px-1 backdrop-blur-sm">
                      {a.error}
                    </div>
                  )}
                  <button
                    data-testid={`remove-attachment-${i}`}
                    onClick={() => onRemoveAttachment?.(i)}
                    className="absolute -top-2 -right-2 h-5 w-5 rounded-full bg-white text-black flex items-center justify-center hover:bg-white/90 shadow-lg"
                  >
                    <X className="h-3 w-3" strokeWidth={2.5} />
                  </button>
                </div>
              ))}
            </div>
          )}
          <div className="flex items-end gap-1.5">
            <input
              ref={fileRef}
              type="file"
              accept="image/png,image/jpeg,image/jpg,image/webp,image/gif,image/heic,image/heif"
              multiple
              onChange={pickFiles}
              className="hidden"
              data-testid="image-input"
            />
            <button
              data-testid="attach-button"
              onClick={openPicker}
              disabled={disabled || blocked || attachments.length >= MAX_IMAGES}
              aria-label="Прикрепить изображение"
              title={attachments.length >= MAX_IMAGES ? `Максимум ${MAX_IMAGES} изображения` : "Прикрепить изображения (до 3, до 20 МБ каждое)"}
              className="h-10 w-10 shrink-0 rounded-2xl text-white/50 hover:text-white hover:bg-white/[0.06] flex items-center justify-center transition-all disabled:opacity-30 disabled:cursor-not-allowed"
            >
              <Paperclip className="h-5 w-5" strokeWidth={1.75} />
            </button>
            <textarea
              ref={ref}
              data-testid="chat-composer-input"
              rows={1}
              value={value}
              disabled={disabled || blocked}
              maxLength={MAX_CHARS}
              onChange={handleChange}
              onKeyDown={handleKey}
              placeholder={cooldownActive ? `Лимит исчерпан — ${cooldownLabel}` : locked ? "Чат заблокирован — 150 МБ" : placeholder}
              className="flex-1 resize-none bg-transparent py-2.5 px-1 text-[15px] text-white placeholder:text-white/35 focus:outline-none max-h-[200px] disabled:cursor-not-allowed"
            />
            {streaming ? (
              <button
                data-testid="stop-button"
                onClick={onStop}
                aria-label="Остановить"
                className="h-10 w-10 shrink-0 rounded-2xl bg-white text-black flex items-center justify-center hover:bg-white/90 transition-all active:scale-95"
              >
                <Square className="h-3.5 w-3.5 fill-current" />
              </button>
            ) : (
              <button
                data-testid="send-button"
                onClick={onSend}
                disabled={!canSend}
                aria-label="Отправить"
                className="h-10 w-10 shrink-0 rounded-2xl bg-white text-black flex items-center justify-center hover:bg-white/90 disabled:opacity-25 disabled:cursor-not-allowed transition-all active:scale-95 shadow-[0_2px_20px_rgba(255,255,255,0.15)]"
              >
                <ArrowUp className="h-4 w-4" strokeWidth={2.5} />
              </button>
            )}
          </div>
        </div>
        <div className="flex items-center justify-between px-1 mt-2">
          <p className="pointer-events-none text-[10px] text-white/30">
            До 3 изображений · до 20 МБ · картинки автоудаляются через 12 ч
          </p>
          <span
            data-testid="char-counter"
            className={`pointer-events-none text-[11px] font-mono tabular-nums ${
              remaining <= 0 ? "text-red-400" : remaining <= 50 ? "text-amber-400" : "text-white/35"
            }`}
          >
            {remaining}
          </span>
        </div>
      </div>
    </div>
  );
}

export const COMPOSER_LIMITS = { MAX_IMAGES, MAX_BYTES, MAX_CHARS };
