"use client";

import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

import { bandOf, type Band } from "./quality-bar";

const CX = 100;
const CY = 100;
const R = 78;
const START = 135; // degrees, measured clockwise from 3 o'clock (SVG y points down)
const SWEEP = 270;

function angleOf(value: number): number {
  return START + (SWEEP * Math.min(100, Math.max(0, value))) / 100;
}

function point(angle: number, radius: number): [number, number] {
  const rad = (angle * Math.PI) / 180;
  return [CX + radius * Math.cos(rad), CY + radius * Math.sin(rad)];
}

function arc(from: number, to: number, radius: number): string {
  const [x0, y0] = point(from, radius);
  const [x1, y1] = point(to, radius);
  const large = to - from > 180 ? 1 : 0;
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${radius} ${radius} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

const STROKE: Record<Band, string> = {
  good: "stroke-good",
  fair: "stroke-warn",
  poor: "stroke-bad",
};

/**
 * The data-health dial: a calibrated 0-100 gauge. Scores increase clockwise in both
 * reading directions (a measuring instrument, not text), so it is not mirrored in RTL.
 */
export function QualityDial({ score, previous }: { score: number; previous?: number | null }) {
  const t = useTranslations("health");
  const band = bandOf(score);
  const [shown, setShown] = useState(previous ?? 0);

  useEffect(() => {
    // One orchestrated moment: the needle settles on the reading after load.
    const id = requestAnimationFrame(() => setShown(score));
    return () => cancelAnimationFrame(id);
  }, [score]);

  const ticks = Array.from({ length: 11 }, (_, i) => i * 10);
  const hasPrevious = previous !== null && previous !== undefined && previous !== score;

  return (
    <div
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={score}
      aria-valuetext={`${t("meterText", { score })}. ${t(`bands.${band}`)}`}
      aria-label={t("scoreLabel")}
      className="relative mx-auto w-full max-w-[15rem]"
      data-testid="quality-dial"
      data-score={score}
    >
      <svg viewBox="0 0 200 186" className="w-full" aria-hidden="true">
        <path d={arc(START, START + SWEEP, R)} className="fill-none stroke-dial-track" strokeWidth="12" strokeLinecap="round" />
        {score > 0 ? (
          <path
            d={arc(START, angleOf(score), R)}
            className={cn("fill-none", STROKE[band])}
            strokeWidth="12"
            strokeLinecap="round"
          />
        ) : null}
        {ticks.map((v) => {
          const a = angleOf(v);
          const major = v % 50 === 0;
          const [x0, y0] = point(a, R - 12);
          const [x1, y1] = point(a, R - (major ? 22 : 17));
          return (
            <line
              key={v}
              x1={x0}
              y1={y0}
              x2={x1}
              y2={y1}
              className="stroke-muted-foreground"
              strokeWidth={major ? 1.6 : 1}
              strokeLinecap="round"
            />
          );
        })}
        {hasPrevious ? (
          <line
            x1={point(angleOf(previous), R + 9)[0]}
            y1={point(angleOf(previous), R + 9)[1]}
            x2={point(angleOf(previous), R - 9)[0]}
            y2={point(angleOf(previous), R - 9)[1]}
            className="stroke-foreground/60"
            strokeWidth="2"
            strokeDasharray="2 2"
          />
        ) : null}
        <g
          style={{
            transform: `rotate(${angleOf(shown)}deg)`,
            transformOrigin: `${CX}px ${CY}px`,
            transition: "transform 900ms cubic-bezier(0.22, 1, 0.36, 1)",
          }}
        >
          <line x1={CX - 10} y1={CY} x2={CX + R - 26} y2={CY} stroke="var(--signal-fill)" strokeWidth="3.5" strokeLinecap="round" />
        </g>
        <circle cx={CX} cy={CY} r="6" className="fill-foreground" />
        <circle cx={CX} cy={CY} r="2.2" fill="var(--signal-fill)" />
      </svg>
      <div className="absolute inset-x-0 bottom-1 flex flex-col items-center">
        <span className="text-4xl leading-none font-semibold tabular-nums">{score}</span>
        <span className="mt-1 text-xs text-muted-foreground">{t("outOf")}</span>
      </div>
    </div>
  );
}
