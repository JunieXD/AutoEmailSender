import clsx from 'clsx';
import { Mail, Target, BrainCircuit } from 'lucide-react';
import { StatusBadge } from '../atoms/StatusBadge';
import { ActionOutlineButton } from '../atoms/ActionOutlineButton';
import type { Mentor } from '../../types';

interface MentorRowCardProps {
  mentor: Mentor;
  isEven: boolean;
  selected: boolean;
  onToggle: (id: string) => void;
  onWriteEmail: (id: string) => void;
}

const MatchIndicator: React.FC<{ score: number }> = ({ score }) => {
  const isHigh = score >= 85;
  const color = isHigh ? 'bg-emerald-600' : 'bg-amber-500';
  const textColor = isHigh ? 'text-emerald-800' : 'text-amber-800';

  return (
    <div className="flex flex-col items-start gap-1 shrink-0">
      <div className="flex items-center gap-1.5 text-xs whitespace-nowrap">
        <Target className={`w-3.5 h-3.5 ${textColor}`} />
        <span className={textColor}>匹配度</span>
        <span className="font-bold text-lg text-primary">{score}</span>
      </div>
      <div className="w-24 h-1.5 bg-stone-100 rounded-full overflow-hidden shadow-inner border border-stone-100">
        <div className={`h-full ${color} transition-all duration-500`} style={{ width: `${score}%` }} />
      </div>
    </div>
  );
};

export const MentorRowCard: React.FC<MentorRowCardProps> = ({
  mentor,
  selected,
  onToggle,
  onWriteEmail,
}) => {
  const maxVisibleResearch = 1;
  const visibleResearch = mentor.research.slice(0, maxVisibleResearch);
  const hiddenResearch = mentor.research.slice(maxVisibleResearch);

  const baseClasses =
    'flex items-center justify-between w-full px-10 py-6 rounded-3xl border border-solid transition-colors duration-150';

  const containerClasses = clsx(
    'flex-1 w-full flex flex-col md:flex-row items-center justify-between p-6 rounded-2xl transition-all duration-200 group',
    'bg-[#FCFBF8] border border-stone-200 shadow-sm hover:shadow-md hover:border-stone-300',
  );

  return (
    <div className="flex items-center gap-4">
      {/* 左侧自定义圆形复选框 */}
      <label className="relative shrink-0 cursor-pointer group">
        <input
          type="checkbox"
          checked={selected}
          onChange={() => onToggle(mentor.id)}
          className="peer sr-only"
        />
        <div className="w-6 h-6 rounded-full border-2 border-stone-300 bg-white transition-all duration-200 group-hover:border-primary peer-checked:border-primary peer-checked:bg-primary peer-focus-visible:ring-2 peer-focus-visible:ring-primary/30" />
        <div className="absolute inset-0 flex items-center justify-center transition-all duration-200 opacity-0 peer-checked:opacity-100">
          <svg className="w-3 h-3 text-white" viewBox="0 0 12 10" fill="none">
            <path d="M1.5 5.5L4.5 8.5L10.5 2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
      </label>

      {/* 圆角矩形卡片 */}
      <div className={`${baseClasses} ${containerClasses}`}>
        <div className="flex flex-col w-64 gap-0.5 shrink-0">
          <span className="text-sm text-stone-500 truncate">
            {mentor.university} / {mentor.school}
          </span>
          <div className="flex items-baseline gap-2.5">
            <span className="text-xl font-bold tracking-wide text-primary-dark whitespace-nowrap">{mentor.name}</span>
            <span className="text-sm font-medium text-stone-600 whitespace-nowrap">{mentor.title}</span>
          </div>
        </div>

        <div className="flex-1 min-w-[180px] flex flex-row items-center gap-2 px-4 md:px-8 border-y md:border-y-0 md:border-x border-stone-100 py-4 md:py-0 overflow-visible">
          {visibleResearch.map((res) => (
            <span
              key={res}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-stone-50 text-stone-600 text-xs font-medium border border-stone-200 whitespace-nowrap"
            >
              <BrainCircuit className="w-3.5 h-3.5 text-stone-400 shrink-0" />
              <span className="truncate max-w-[150px]">{res}</span>
            </span>
          ))}

          {hiddenResearch.length > 0 && (
            <div className="relative z-20 ml-1 shrink-0 group/tooltip">
              <button
                type="button"
                aria-label={`查看剩余 ${hiddenResearch.length} 个研究方向`}
                className="inline-flex items-center justify-center px-2 py-1.5 rounded-lg bg-stone-100 text-stone-500 text-xs font-bold border border-stone-200 shadow-sm cursor-help whitespace-nowrap transition-colors hover:bg-stone-200 focus:outline-none focus:ring-2 focus:ring-stone-300"
              >
                +{hiddenResearch.length}
              </button>

              <div className="pointer-events-none absolute left-1/2 bottom-full z-30 mb-3 w-max max-w-[20rem] -translate-x-1/2 translate-y-1 rounded-2xl bg-stone-950 px-4 py-3 text-white shadow-2xl opacity-0 transition-all duration-200 group-hover/tooltip:pointer-events-auto group-hover/tooltip:translate-y-0 group-hover/tooltip:opacity-100 group-focus-within/tooltip:pointer-events-auto group-focus-within/tooltip:translate-y-0 group-focus-within/tooltip:opacity-100">
                <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-stone-400">
                  其余研究方向
                </div>
                <div className="flex max-w-[20rem] flex-wrap gap-1.5">
                  {hiddenResearch.map((item) => (
                    <span
                      key={item}
                      className="inline-flex items-center rounded-full border border-white/10 bg-white/8 px-2.5 py-1 text-[11px] leading-none text-stone-100"
                    >
                      {item}
                    </span>
                  ))}
                </div>
                <div className="absolute left-1/2 top-full h-3 w-3 -translate-x-1/2 -translate-y-1/2 rotate-45 bg-stone-950" />
              </div>
            </div>
          )}

          {mentor.research.length === 0 && (
            <span className="text-xs text-stone-400 italic">暂无研究方向数据</span>
          )}
        </div>

        <div className="flex items-center gap-8 px-8 shrink-0">
          <MatchIndicator score={mentor.matchScore} />

          <div className="flex flex-col items-center shrink-0">
            <span className="text-xs text-stone-500">发送</span>
            <span className="font-bold text-3xl mt-0.5 text-primary">{mentor.sentCount}</span>
          </div>

          <div className="flex flex-col items-center shrink-0">
            <span className="text-xs text-stone-500">状态</span>
            <div className="mt-1.5">
              <StatusBadge status={mentor.status} />
            </div>
          </div>
        </div>

        <div className="flex items-center pl-8 justify-end shrink-0">
          <ActionOutlineButton
            label="写邮件"
            icon={<Mail className="w-4 h-4" />}
            onClick={() => onWriteEmail(mentor.id)}
          />
        </div>
      </div>
    </div>
  );
};
