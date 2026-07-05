import React from "react";
import { ChevronDown, Sparkles, Rocket, Zap } from "lucide-react";
import {
  Select, SelectContent, SelectItem, SelectTrigger,
} from "@/components/ui/select";

const KIND_META = {
  "gemini-2.5-flash":       { Icon: Zap,      chip: "bg-emerald-500/10 text-emerald-300 border-emerald-500/20" },
  "gemini-2.5-flash-lite":  { Icon: Sparkles, chip: "bg-sky-500/10 text-sky-300 border-sky-500/20" },
  "gemini-2.5-pro":         { Icon: Rocket,   chip: "bg-violet-500/10 text-violet-300 border-violet-500/20" },
};

export default function ModelSelector({ models, value, onChange }) {
  const active = models.find((m) => m.id === value);
  const meta = KIND_META[value] || { Icon: Sparkles, chip: "bg-white/10 text-white/70 border-white/10" };
  const Icon = meta.Icon;

  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger
        data-testid="model-selector"
        className="h-9 w-auto max-w-[220px] gap-2 rounded-xl border-white/10 bg-white/[0.04] hover:bg-white/[0.07] text-sm text-white focus:ring-white/20 transition-colors"
      >
        <span className={`h-6 w-6 shrink-0 rounded-md border ${meta.chip} flex items-center justify-center`}>
          <Icon className="h-3.5 w-3.5" strokeWidth={2} />
        </span>
        <span className="truncate font-medium">{active?.name || "Модель"}</span>
        <ChevronDown className="h-4 w-4 shrink-0 text-white/40" />
      </SelectTrigger>
      <SelectContent className="bg-[#0F0F11] border-white/10 text-white max-w-[calc(100vw-2rem)] rounded-xl overflow-hidden p-0">
        {models.map((m) => {
          const mMeta = KIND_META[m.id] || { Icon: Sparkles, chip: "bg-white/10 text-white/70 border-white/10" };
          const MIcon = mMeta.Icon;
          return (
            <SelectItem key={m.id} value={m.id} data-testid={`model-option-${m.id}`} className="focus:bg-white/[0.06] cursor-pointer py-2.5">
              <div className="flex items-start gap-3 pr-4">
                <span className={`mt-0.5 h-7 w-7 shrink-0 rounded-lg border ${mMeta.chip} flex items-center justify-center`}>
                  <MIcon className="h-3.5 w-3.5" strokeWidth={2} />
                </span>
                <div className="flex flex-col min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium truncate">{m.name}</span>
                    <span className={`text-[9px] font-mono px-1.5 py-0.5 rounded border ${mMeta.chip}`}>{m.badge}</span>
                  </div>
                  <span className="mt-0.5 text-[11.5px] text-white/50 leading-snug">{m.desc}</span>
                </div>
              </div>
            </SelectItem>
          );
        })}
        <div data-testid="model-footer-note" className="px-3 py-2.5 border-t border-white/[0.06] bg-white/[0.015]">
          <p className="text-[10.5px] leading-snug text-white/45">
            Модели будут дополняться. <span className="text-white/70 font-mono uppercase tracking-wider">Beta</span>
          </p>
        </div>
      </SelectContent>
    </Select>
  );
}
