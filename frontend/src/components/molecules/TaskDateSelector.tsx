import { useMemo, useState } from 'react';
import clsx from 'clsx';
import {
  applyDateRule,
  getWorkdayStatus,
  isValidIsoDate,
  normalizeScheduledDates,
  toggleScheduledDate,
  type DateRule,
} from '@/features/create-task/client/scheduleDates';

interface TaskDateSelectorProps {
  selectedDates: string[];
  onChange: (dates: string[]) => void;
}

const ruleLabels: Array<{ label: string; value: DateRule }> = [
  { label: '每天', value: 'all' },
  { label: '工作日', value: 'weekdays' },
  { label: '周一三五', value: 'mon-wed-fri' },
  { label: '周末', value: 'weekends' },
];

const weekdayLabels = ['一', '二', '三', '四', '五', '六', '日'];
const maxRuleRangeDays = 366;
const millisecondsPerDay = 24 * 60 * 60 * 1000;

const toIsoDate = (date: Date) => date.toISOString().slice(0, 10);
const fromIsoDate = (value: string) => new Date(`${value}T00:00:00Z`);

const getTodayIsoDate = () => {
  const today = new Date();
  // time-check: local-control-value, calendar grid constructs UTC dates from local date parts.
  return toIsoDate(new Date(Date.UTC(today.getFullYear(), today.getMonth(), today.getDate())));
};

const buildMonthDays = (monthDate: Date) => {
  const year = monthDate.getUTCFullYear();
  const month = monthDate.getUTCMonth();
  // time-check: local-control-value, calendar grid month calculations use UTC date objects.
  const firstDay = new Date(Date.UTC(year, month, 1));
  const startOffset = (firstDay.getUTCDay() + 6) % 7;
  // time-check: local-control-value, calendar grid start date is derived from visible month.
  const start = new Date(Date.UTC(year, month, 1 - startOffset));

  return Array.from({ length: 42 }, (_, index) => {
    // time-check: local-control-value, calendar cells clone an internal Date object.
    const date = new Date(start);
    date.setUTCDate(start.getUTCDate() + index);
    return date;
  });
};

const getInclusiveRangeDays = (startDate: string, endDate: string) =>
  Math.floor((fromIsoDate(endDate).getTime() - fromIsoDate(startDate).getTime()) / millisecondsPerDay) + 1;

export const TaskDateSelector: React.FC<TaskDateSelectorProps> = ({
  selectedDates,
  onChange,
}) => {
  const [todayIso] = useState(() => getTodayIsoDate());
  const [rangeStart, setRangeStart] = useState(todayIso);
  const [rangeEnd, setRangeEnd] = useState(todayIso);
  const [visibleMonth, setVisibleMonth] = useState(() => fromIsoDate(todayIso));
  const [ruleError, setRuleError] = useState('');

  const normalizedSelectedDates = useMemo(
    () => normalizeScheduledDates(selectedDates),
    [selectedDates],
  );
  const selectedDateSet = useMemo(
    () => new Set(normalizedSelectedDates),
    [normalizedSelectedDates],
  );
  const monthDays = useMemo(() => buildMonthDays(visibleMonth), [visibleMonth]);

  const visibleYear = visibleMonth.getUTCFullYear();
  const visibleMonthIndex = visibleMonth.getUTCMonth();
  const monthLabel = `${visibleYear}年${visibleMonthIndex + 1}月`;

  const changeMonth = (offset: number) => {
    // time-check: local-control-value, calendar navigation constructs the visible month.
    setVisibleMonth(new Date(Date.UTC(visibleYear, visibleMonthIndex + offset, 1)));
  };

  const handleRuleClick = (rule: DateRule) => {
    if (!isValidIsoDate(rangeStart) || !isValidIsoDate(rangeEnd)) {
      setRuleError('请选择有效的日期范围');
      return;
    }
    if (rangeStart > rangeEnd) {
      setRuleError('开始日期不能晚于结束日期');
      return;
    }
    if (getInclusiveRangeDays(rangeStart, rangeEnd) > maxRuleRangeDays) {
      setRuleError('日期范围最多支持 366 天');
      return;
    }

    setRuleError('');
    onChange(applyDateRule(rule, rangeStart, rangeEnd));
  };

  const handleDateClick = (date: Date) => {
    setRuleError('');
    onChange(toggleScheduledDate(normalizedSelectedDates, toIsoDate(date)));
  };

  return (
    <div className="flex flex-col gap-4">
      <div>
        <p className="text-sm font-semibold text-stone-700">发送日期</p>
        <p className="mt-1 text-xs text-stone-500">
          已选 {normalizedSelectedDates.length} 天。日历中高亮的日期会被安排发送。
        </p>
      </div>

      <div className="rounded-2xl border border-stone-200 bg-stone-50/80 p-4">
        <div>
          <p className="text-sm font-semibold text-stone-800">按范围快速选择</p>
          <p className="mt-1 text-xs leading-5 text-stone-500">
            先设置起止日期，再用规则批量生成发送日期。
          </p>
        </div>

        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1.5 text-xs font-medium text-stone-500">
            起始日期
            <input
              type="date"
              value={rangeStart}
              onChange={(event) => setRangeStart(event.target.value)}
              className="h-9 rounded-lg border border-stone-200 bg-white px-3 text-sm text-stone-700 transition-all hover:border-stone-300 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
          </label>
          <label className="flex flex-col gap-1.5 text-xs font-medium text-stone-500">
            结束日期
            <input
              type="date"
              value={rangeEnd}
              onChange={(event) => setRangeEnd(event.target.value)}
              className="h-9 rounded-lg border border-stone-200 bg-white px-3 text-sm text-stone-700 transition-all hover:border-stone-300 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
          </label>
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          {ruleLabels.map((rule) => (
            <button
              key={rule.value}
              type="button"
              onClick={() => handleRuleClick(rule.value)}
              className="h-8 rounded-lg border border-stone-200 bg-white px-3 text-xs font-medium text-stone-600 transition-all hover:border-primary/40 hover:bg-primary/5 hover:text-primary"
            >
              {rule.label}
            </button>
          ))}
          <button
            type="button"
            onClick={() => {
              setRuleError('');
              onChange([]);
            }}
            className="h-8 rounded-lg border border-stone-200 bg-white px-3 text-xs font-medium text-stone-500 transition-all hover:border-stone-300 hover:text-stone-700"
          >
            清空重选
          </button>
        </div>
        {ruleError && <p className="mt-2 text-xs text-red-500">{ruleError}</p>}
      </div>

      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h3 className="text-sm font-semibold text-stone-800">{monthLabel}</h3>
            <p className="mt-1 text-xs leading-5 text-stone-500">
              日历中高亮的日期会被安排发送；点击某一天可单独加入或移除。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => changeMonth(-1)}
              className="h-8 rounded-lg border border-stone-200 bg-white px-3 text-xs font-medium text-stone-600 transition-all hover:border-stone-300"
            >
              上月
            </button>
            <button
              type="button"
              onClick={() => changeMonth(1)}
              className="h-8 rounded-lg border border-stone-200 bg-white px-3 text-xs font-medium text-stone-600 transition-all hover:border-stone-300"
            >
              下月
            </button>
          </div>
        </div>

        <div className="flex flex-wrap gap-3 text-[11px] text-stone-500">
          <span className="inline-flex items-center gap-1">
            <span className="h-2.5 w-2.5 rounded-sm bg-primary" />
            高亮日期会发送
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="rounded-sm border border-stone-200 bg-stone-50 px-1 text-[10px] font-semibold text-stone-500">
              休
            </span>
            休息日
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="rounded-sm border border-emerald-200 bg-emerald-50 px-1 text-[10px] font-semibold text-emerald-700">
              班
            </span>
            调休补班
          </span>
        </div>

        <div className="grid grid-cols-7 gap-1 text-center">
          {weekdayLabels.map((label) => (
            <div key={label} className="py-1 text-xs font-medium text-stone-400">
              {label}
            </div>
          ))}
          {monthDays.map((date) => {
            const isoDate = toIsoDate(date);
            const isSelected = selectedDateSet.has(isoDate);
            const isOutsideMonth = date.getUTCMonth() !== visibleMonthIndex;
            const isToday = isoDate === todayIso;
            const workdayStatus = getWorkdayStatus(isoDate);
            const isRestDay = workdayStatus === 'rest';
            const isAdjustedWorkday = workdayStatus === 'adjusted-workday';
            const dateStateLabel = [
              isoDate,
              isRestDay ? '休息日' : null,
              isAdjustedWorkday ? '调休补班' : null,
              isOutsideMonth ? '非本月' : null,
              isSelected ? '已选中' : '未选中',
            ].filter(Boolean).join('，');

            return (
              <button
                key={isoDate}
                type="button"
                onClick={() => handleDateClick(date)}
                aria-pressed={isSelected}
                aria-label={dateStateLabel}
                className={clsx(
                  'flex h-12 flex-col items-center justify-center rounded-md border text-sm font-medium transition-all',
                  isSelected
                    ? 'border-primary bg-primary text-white shadow-sm'
                    : isOutsideMonth
                      ? 'border-stone-100 bg-stone-50 text-stone-400 hover:border-primary/30 hover:bg-primary/5'
                      : 'border-stone-200 bg-white text-stone-700 hover:border-primary/50 hover:bg-primary/5',
                  isOutsideMonth && 'opacity-50',
                  isToday && !isSelected && !isOutsideMonth && 'border-primary/50 text-primary',
                )}
              >
                <span className="leading-5">{date.getUTCDate()}</span>
                <span
                  className={clsx(
                    'min-h-3 text-[10px] leading-3',
                    isSelected
                      ? 'text-white/80'
                      : isRestDay
                        ? 'text-amber-700'
                        : isAdjustedWorkday
                          ? 'text-emerald-700'
                          : 'text-transparent',
                  )}
                  aria-hidden="true"
                >
                  {isRestDay ? '休' : isAdjustedWorkday ? '班' : ''}
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
};
