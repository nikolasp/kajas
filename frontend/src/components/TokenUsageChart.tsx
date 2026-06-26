import { useMemo } from "react";
import { fmtNumber } from "../lib/format";
import type { RunSummary } from "../lib/types";

interface TokenUsagePoint {
  run: RunSummary;
  cumulative: number;
}

export function TokenUsageChart({ runs }: { runs: RunSummary[] }) {
  const usagePoints = useMemo(() => {
    const chronological = [...runs]
      .filter((run) => (run.total_tokens ?? 0) > 0)
      .sort((a, b) => {
        const aTime = new Date(a.started_at || a.updated_at).getTime();
        const bTime = new Date(b.started_at || b.updated_at).getTime();
        return aTime - bTime;
      });

    let cumulative = 0;
    return chronological.map<TokenUsagePoint>((run) => {
      cumulative += run.total_tokens ?? 0;
      return { run, cumulative };
    });
  }, [runs]);

  const accumulatedTokens =
    usagePoints.length > 0
      ? usagePoints[usagePoints.length - 1].cumulative
      : null;

  return (
    <div className="panel overflow-hidden">
      <div className="panel-header">
        <h2 className="text-sm font-semibold text-ink-200">Token Usage</h2>
        <span className="font-mono text-sm text-ink-100">
          {fmtNumber(accumulatedTokens)}
        </span>
      </div>
      <div className="panel-body">
        {usagePoints.length === 0 ? (
          <div className="flex h-36 items-center text-sm text-ink-400">
            No token usage recorded yet.
          </div>
        ) : (
          <TokenUsageSvg points={usagePoints} />
        )}
      </div>
    </div>
  );
}

function TokenUsageSvg({ points }: { points: TokenUsagePoint[] }) {
  const maxCumulative = Math.max(1, ...points.map((point) => point.cumulative));
  const chartWidth = 640;
  const chartHeight = 180;
  const chartPadX = 58;
  const chartPadTop = 18;
  const chartPadBottom = 34;
  const plotWidth = chartWidth - chartPadX - 16;
  const plotHeight = chartHeight - chartPadTop - chartPadBottom;
  const xForIndex = (index: number) =>
    chartPadX +
    (points.length === 1
      ? plotWidth
      : (index / (points.length - 1)) * plotWidth);
  const yForValue = (value: number) =>
    chartPadTop + plotHeight - (value / maxCumulative) * plotHeight;
  const chartLine = points
    .map((point, index) => `${xForIndex(index)},${yForValue(point.cumulative)}`)
    .join(" ");
  const chartArea = `${chartPadX},${
    chartHeight - chartPadBottom
  } ${chartLine} ${chartWidth - 16},${chartHeight - chartPadBottom}`;
  const gridLines = [0, 0.33, 0.66, 1].map((ratio) => {
    const value = Math.round(maxCumulative * ratio);
    return { value, y: yForValue(value) };
  });
  const dateTickIndexes =
    points.length <= 4
      ? points.map((_, index) => index)
      : Array.from(
          new Set([
            0,
            Math.round((points.length - 1) / 3),
            Math.round(((points.length - 1) * 2) / 3),
            points.length - 1,
          ]),
        );

  return (
    <svg
      className="h-44 w-full overflow-visible"
      viewBox={`0 0 ${chartWidth} ${chartHeight}`}
      role="img"
      aria-label="Cumulative token usage across runs"
      preserveAspectRatio="none"
    >
      {gridLines.map((line) => (
        <g key={line.value}>
          <line
            x1={chartPadX}
            y1={line.y}
            x2={chartWidth - 16}
            y2={line.y}
            className="stroke-ink-700/80"
            strokeWidth="1"
          />
          <text
            x={chartPadX - 10}
            y={line.y + 4}
            textAnchor="end"
            className="fill-ink-400 font-mono text-[10px]"
          >
            {fmtNumber(line.value)}
          </text>
        </g>
      ))}
      <polygon points={chartArea} className="fill-accent-600/15" />
      <polyline
        points={chartLine}
        className="fill-none stroke-accent-400"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {points.map((point, index) => (
        <circle
          key={point.run.id}
          cx={xForIndex(index)}
          cy={yForValue(point.cumulative)}
          r={index === points.length - 1 ? 4 : 2.5}
          className="fill-accent-400"
        />
      ))}
      {dateTickIndexes.map((pointIndex, index) => {
        const point = points[pointIndex];
        const x = xForIndex(pointIndex);
        return (
          <g key={`${point.run.id}-date`}>
            <line
              x1={x}
              y1={chartHeight - chartPadBottom}
              x2={x}
              y2={chartHeight - chartPadBottom + 5}
              className="stroke-ink-600"
              strokeWidth="1"
            />
            <text
              x={x}
              y={chartHeight - 10}
              textAnchor={
                index === 0
                  ? "start"
                  : index === dateTickIndexes.length - 1
                    ? "end"
                    : "middle"
              }
              className="fill-ink-400 text-[10px]"
            >
              {shortDate(point.run.started_at || point.run.updated_at)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function shortDate(iso: string) {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
  }).format(new Date(iso));
}
