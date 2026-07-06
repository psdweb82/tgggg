import React, { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Sparkles } from "lucide-react";

/**
 * Приветственный экран:
 *  1) лого плавно появляется + заголовок AI Workspace;
 *  2) появляется надпись «Пора сделать ваш первый запрос»;
 *  3) текст исчезает, лого плавно уходит назад;
 *  4) оверлей затухает — открывается основной интерфейс.
 */
export default function IntroSplash({ onDone }) {
  const [phase, setPhase] = useState("in"); // in -> hold -> out

  useEffect(() => {
    const t1 = setTimeout(() => setPhase("hold"), 400);
    const t2 = setTimeout(() => setPhase("out"), 2400);
    const t3 = setTimeout(() => onDone?.(), 3200);
    return () => { clearTimeout(t1); clearTimeout(t2); clearTimeout(t3); };
  }, [onDone]);

  return (
    <motion.div
      data-testid="intro-splash"
      className="fixed inset-0 z-[100] flex flex-col items-center justify-center bg-[#070708]"
      initial={{ opacity: 1 }}
      animate={{ opacity: phase === "out" ? 0 : 1 }}
      transition={{ duration: 0.75, ease: [0.22, 1, 0.36, 1] }}
    >
      <div className="pointer-events-none absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 h-[420px] w-[420px] rounded-full bg-white/[0.05] blur-[120px]" />

      <motion.div
        initial={{ opacity: 0, scale: 0.55, y: 12 }}
        animate={{
          opacity: 1,
          scale: phase === "out" ? 0.8 : 1,
          y: phase === "out" ? -14 : 0,
        }}
        transition={{ duration: 0.85, ease: [0.22, 1, 0.36, 1] }}
        className="relative h-20 w-20 rounded-3xl bg-gradient-to-br from-white to-white/70 text-black flex items-center justify-center shadow-[0_20px_80px_rgba(255,255,255,0.18)]"
      >
        <Sparkles className="h-10 w-10" strokeWidth={1.5} />
        <span className="absolute inset-0 rounded-3xl ring-1 ring-white/10" />
      </motion.div>

      <motion.h1
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: phase === "out" ? 0 : 1, y: 0 }}
        transition={{ delay: 0.55, duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
        className="font-display text-3xl sm:text-4xl font-medium tracking-tight text-white mt-7"
      >
        AI <span className="bg-gradient-to-r from-white to-white/50 bg-clip-text text-transparent">Workspace</span>
      </motion.h1>

      <AnimatePresence>
        {phase === "hold" && (
          <motion.p
            data-testid="intro-tagline"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
            className="text-white/55 mt-3 text-sm sm:text-base"
          >
            Пора сделать ваш первый запрос
          </motion.p>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
