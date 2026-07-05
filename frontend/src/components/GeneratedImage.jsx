import React, { useEffect, useState } from "react";
import { Download, Maximize2, X } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

async function downloadDataUrl(url, filename) {
  try {
    // Works for both data: URLs and cross-origin http(s) URLs
    const res = await fetch(url);
    const blob = await res.blob();
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
  } catch {
    // Fallback — best effort
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.target = "_blank";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
}

/**
 * A generated image tile with download + fullscreen viewer.
 * `src` can be a full data URL or the raw base64 (in which case we build the data URL from `mime`).
 */
export default function GeneratedImage({ src, mime = "image/png", alt = "Сгенерировано" }) {
  const [open, setOpen] = useState(false);
  const dataUrl = !src
    ? ""
    : (src.startsWith("data:") || src.startsWith("http") || src.startsWith("blob:"))
      ? src
      : `data:${mime};base64,${src}`;
  const fileExt = (mime.split("/")[1] || "png").split("+")[0];
  const filename = `ai-image-${Date.now()}.${fileExt}`;

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open]);

  return (
    <>
      <div
        data-testid="generated-image"
        className="group relative inline-block max-w-full overflow-hidden rounded-2xl border border-white/10 bg-black/40 shadow-[0_10px_40px_rgba(0,0,0,0.4)]"
      >
        <img
          src={dataUrl}
          alt={alt}
          onClick={() => setOpen(true)}
          className="block max-h-[420px] w-auto max-w-full cursor-zoom-in object-contain transition-transform duration-500 group-hover:scale-[1.015]"
        />
        <div className="absolute inset-x-0 bottom-0 flex items-center justify-between gap-2 bg-gradient-to-t from-black/70 via-black/30 to-transparent px-3 py-2 opacity-0 group-hover:opacity-100 transition-opacity">
          <span className="text-[11px] uppercase tracking-wider text-white/60 font-mono">Nano Banana</span>
          <div className="flex items-center gap-1">
            <button
              data-testid="image-fullscreen-button"
              onClick={() => setOpen(true)}
              className="inline-flex items-center gap-1 rounded-lg bg-white/10 hover:bg-white/20 backdrop-blur px-2.5 py-1.5 text-[11px] text-white transition-colors"
            >
              <Maximize2 className="h-3.5 w-3.5" />
              На весь экран
            </button>
            <button
              data-testid="image-download-button"
              onClick={() => downloadDataUrl(dataUrl, filename)}
              className="inline-flex items-center gap-1 rounded-lg bg-white text-black hover:bg-white/90 px-2.5 py-1.5 text-[11px] font-medium transition-colors"
            >
              <Download className="h-3.5 w-3.5" />
              Скачать
            </button>
          </div>
        </div>
      </div>

      <AnimatePresence>
        {open && (
          <motion.div
            data-testid="image-lightbox"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-0 z-[100] bg-black/95 backdrop-blur-xl flex items-center justify-center p-4"
            onClick={() => setOpen(false)}
          >
            <button
              data-testid="lightbox-close"
              onClick={(e) => { e.stopPropagation(); setOpen(false); }}
              className="absolute top-4 right-4 h-10 w-10 rounded-full bg-white/10 hover:bg-white/20 flex items-center justify-center text-white transition-colors"
            >
              <X className="h-5 w-5" />
            </button>
            <button
              data-testid="lightbox-download"
              onClick={(e) => { e.stopPropagation(); downloadDataUrl(dataUrl, filename); }}
              className="absolute top-4 right-16 inline-flex items-center gap-1.5 rounded-full bg-white text-black hover:bg-white/90 px-4 h-10 text-sm font-medium transition-colors"
            >
              <Download className="h-4 w-4" />
              Скачать
            </button>
            <motion.img
              initial={{ scale: 0.94, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.94, opacity: 0 }}
              transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
              src={dataUrl}
              alt={alt}
              onClick={(e) => e.stopPropagation()}
              className="max-h-[92vh] max-w-[92vw] rounded-lg shadow-2xl"
            />
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
