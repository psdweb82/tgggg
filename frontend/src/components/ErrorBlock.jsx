import React from "react";
import { AlertTriangle, RotateCw } from "lucide-react";

/**
 * A stylish inline error card for AI errors (quota, safety, network).
 * Used both while streaming (as an inline event) and as a standalone message.
 */
export default function ErrorBlock({ message, onRetry, variant = "error" }) {
  const isStop = variant === "stop";
  const palette = isStop
    ? {
        border: "border-white/10",
        bg: "bg-white/[0.03]",
        icon: "text-white/60",
        title: "Запрос остановлен",
        titleColor: "text-white/80",
      }
    : {
        border: "border-red-500/25",
        bg: "bg-gradient-to-br from-red-500/[0.08] via-red-500/[0.04] to-transparent",
        icon: "text-red-400",
        title: "Что-то пошло не так",
        titleColor: "text-red-200",
      };

  return (
    <div
      data-testid={isStop ? "stopped-block" : "error-block"}
      className={`relative overflow-hidden rounded-2xl border ${palette.border} ${palette.bg} px-4 py-3.5 backdrop-blur-sm`}
    >
      <div className="flex items-start gap-3">
        <div className={`mt-0.5 h-8 w-8 shrink-0 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center ${palette.icon}`}>
          <AlertTriangle className="h-4 w-4" strokeWidth={2} />
        </div>
        <div className="flex-1 min-w-0">
          <p className={`text-[13px] font-medium ${palette.titleColor} tracking-tight`}>{palette.title}</p>
          <p className="mt-1 text-[13px] leading-relaxed text-white/70 break-words">{message}</p>
          {onRetry && !isStop && (
            <button
              data-testid="retry-button"
              onClick={onRetry}
              className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-white/15 bg-white/5 hover:bg-white/10 px-3 py-1.5 text-[12px] font-medium text-white/90 transition-all hover:-translate-y-[1px] active:translate-y-0"
            >
              <RotateCw className="h-3.5 w-3.5" />
              Повторить
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
