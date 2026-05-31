import { useMemo, useState } from 'react';
import clsx from 'clsx';
import { assignPieSliceColors } from '@/lib/charting';

export interface DistributionPieChartItem {
  key: string;
  label: string;
  count: number;
}

interface DistributionPieChartProps {
  title?: string;
  data: DistributionPieChartItem[];
  emptyText: string;
  className?: string;
  valueSuffix?: string;
  legendLayout?: 'compact' | 'columns' | 'horizontal-scroll';
}

const formatPercent = (value: number) => `${Math.round(value * 100)}%`;

const polarToCartesian = (center: number, radius: number, angle: number) => {
  const radians = ((angle - 90) * Math.PI) / 180;
  return {
    x: center + radius * Math.cos(radians),
    y: center + radius * Math.sin(radians),
  };
};

const describeArc = (center: number, radius: number, startAngle: number, endAngle: number) => {
  const start = polarToCartesian(center, radius, endAngle);
  const end = polarToCartesian(center, radius, startAngle);
  const largeArcFlag = endAngle - startAngle <= 180 ? '0' : '1';
  return [
    `M ${center} ${center}`,
    `L ${start.x} ${start.y}`,
    `A ${radius} ${radius} 0 ${largeArcFlag} 0 ${end.x} ${end.y}`,
    'Z',
  ].join(' ');
};

const formatCount = (count: number, suffix?: string) => (suffix ? `${count} ${suffix}` : String(count));

type PieChartSlice = DistributionPieChartItem & {
  color: string;
  startAngle: number;
  endAngle: number;
  percent: number;
};

export const DistributionPieChart = ({
  title,
  data,
  emptyText,
  className,
  valueSuffix,
  legendLayout = 'compact',
}: DistributionPieChartProps) => {
  const [hoveredKey, setHoveredKey] = useState<string | null>(null);
  const total = data.reduce((sum, item) => sum + item.count, 0);
  const slices = useMemo(() => {
    const visibleData = data.filter((item) => item.count > 0);
    const sliceColors = assignPieSliceColors(visibleData.length);

    return visibleData.reduce<PieChartSlice[]>((acc, item, index) => {
      const startAngle = acc.length > 0 ? acc[acc.length - 1].endAngle : 0;
      const percent = item.count / total;
      const angle = percent * 360;

      return [
        ...acc,
        {
          ...item,
          color: sliceColors[index],
          startAngle,
          endAngle: startAngle + angle,
          percent,
        },
      ];
    }, []);
  }, [data, total]);

  const hoveredItem = slices.find((item) => item.key === hoveredKey) ?? null;

  if (total === 0) {
    return (
      <div className="flex min-h-48 items-center justify-center rounded-xl border border-dashed border-stone-200 bg-stone-50 text-sm text-stone-500">
        {emptyText}
      </div>
    );
  }

  return (
    <div className={clsx('relative grid gap-4 md:grid-cols-[12rem_minmax(0,1fr)]', className)}>
      <div className="flex items-center justify-center">
        <svg viewBox="0 0 120 120" className="h-48 w-48" role="img" aria-label={title}>
          {slices.map((item) => {
            const active = hoveredKey === null || hoveredKey === item.key;
            if (item.percent >= 1) {
              return (
                <circle
                  key={item.key}
                  data-testid={`pie-full-slice-${item.key}`}
                  cx="60"
                  cy="60"
                  r={hoveredKey === item.key ? 58 : 54}
                  fill={item.color}
                  className="cursor-pointer transition-opacity duration-150"
                  opacity={active ? 1 : 0.35}
                  onMouseEnter={() => setHoveredKey(item.key)}
                  onMouseLeave={() => setHoveredKey(null)}
                />
              );
            }
            return (
              <path
                key={item.key}
                data-testid={`pie-slice-${item.key}`}
                d={describeArc(60, hoveredKey === item.key ? 58 : 54, item.startAngle, item.endAngle)}
                fill={item.color}
                className="cursor-pointer transition-opacity duration-150"
                opacity={active ? 1 : 0.35}
                onMouseEnter={() => setHoveredKey(item.key)}
                onMouseLeave={() => setHoveredKey(null)}
              />
            );
          })}
        </svg>
      </div>
      <PieLegend
        slices={slices}
        layout={legendLayout}
        valueSuffix={valueSuffix}
        onHover={setHoveredKey}
      />
      {hoveredItem ? (
        <div role="tooltip" className="pointer-events-none absolute left-4 top-4 rounded-lg bg-stone-950 px-3 py-2 text-xs text-white shadow-lg">
          <div className="font-semibold">{hoveredItem.label}</div>
          <div className="mt-1 text-stone-200">
            {formatCount(hoveredItem.count, valueSuffix)} · {formatPercent(hoveredItem.percent)}
          </div>
        </div>
      ) : null}
    </div>
  );
};

function PieLegend({
  slices,
  layout,
  valueSuffix,
  onHover,
}: {
  slices: PieChartSlice[];
  layout: 'compact' | 'columns' | 'horizontal-scroll';
  valueSuffix?: string;
  onHover: (key: string | null) => void;
}) {
  if (layout === 'columns') {
    return (
      <div data-testid="pie-legend-columns" className="max-h-64 space-y-2 overflow-y-auto pr-2">
        {slices.map((item) => (
          <button
            key={item.key}
            type="button"
            data-testid={`pie-legend-row-${item.key}`}
            className="grid w-full grid-cols-[minmax(0,1fr)_5rem_4rem] items-center gap-3 rounded-lg px-2 py-1.5 text-left text-xs transition-colors hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30"
            onMouseEnter={() => onHover(item.key)}
            onMouseLeave={() => onHover(null)}
            onFocus={() => onHover(item.key)}
            onBlur={() => onHover(null)}
          >
            <span className="inline-flex min-w-0 items-center gap-2 text-left">
              <span className="h-3 w-3 shrink-0 rounded-full" style={{ backgroundColor: item.color }} />
              <span className="truncate font-medium text-stone-700" title={item.label}>
                {item.label}
              </span>
            </span>
            <span className="text-left font-medium text-stone-900">
              {formatCount(item.count, valueSuffix)}
            </span>
            <span className="text-left text-stone-500">{formatPercent(item.percent)}</span>
          </button>
        ))}
      </div>
    );
  }

  if (layout === 'horizontal-scroll') {
    return (
      <div
        data-testid="pie-legend-horizontal-scroll"
        className="min-w-0 overflow-x-auto overflow-y-hidden pb-2"
      >
        <div className="w-max min-w-full space-y-2 pr-2">
          {slices.map((item) => (
            <button
              key={item.key}
              type="button"
              data-testid={`pie-legend-scroll-row-${item.key}`}
              className="grid w-max min-w-full grid-cols-[0.75rem_max-content_auto_auto] items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs transition-colors hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30"
              onMouseEnter={() => onHover(item.key)}
              onMouseLeave={() => onHover(null)}
              onFocus={() => onHover(item.key)}
              onBlur={() => onHover(null)}
            >
              <span className="h-3 w-3 rounded-full" style={{ backgroundColor: item.color }} />
              <span className="whitespace-nowrap font-medium text-stone-700" title={item.label}>
                {item.label}
              </span>
              <span className="whitespace-nowrap font-medium text-stone-900">
                {formatCount(item.count, valueSuffix)}
              </span>
              <span className="whitespace-nowrap text-stone-500">{formatPercent(item.percent)}</span>
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="max-h-56 space-y-2 overflow-y-auto pr-1">
      {slices.map((item) => (
        <button
          key={item.key}
          type="button"
          className="grid w-full grid-cols-[0.75rem_minmax(0,1fr)_auto_auto] items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs transition-colors hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30"
          onMouseEnter={() => onHover(item.key)}
          onMouseLeave={() => onHover(null)}
          onFocus={() => onHover(item.key)}
          onBlur={() => onHover(null)}
        >
          <span className="h-3 w-3 rounded-full" style={{ backgroundColor: item.color }} />
          <span className="truncate font-medium text-stone-700" title={item.label}>
            {item.label}
          </span>
          <span className="font-medium text-stone-900">{formatCount(item.count, valueSuffix)}</span>
          <span className="text-stone-500">{formatPercent(item.percent)}</span>
        </button>
      ))}
    </div>
  );
}
