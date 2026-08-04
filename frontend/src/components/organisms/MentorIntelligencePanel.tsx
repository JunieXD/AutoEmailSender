import { BrainCircuit } from 'lucide-react';
import type { ReactNode } from 'react';
import { MentorContextItem } from '../atoms/MentorContextItem';
import { StatusBadge } from '../atoms/StatusBadge';
import type { Mentor } from '@/types';

interface MentorIntelligencePanelProps {
  mentor: Mentor;
}

const SectionTitle = ({ label, icon }: { label: string; icon: ReactNode }) => (
  <h3 className="flex items-center gap-2.5 text-sm font-bold text-stone-700 mt-6 mb-3 select-none">
    {icon}
    {label}
  </h3>
);

export const MentorIntelligencePanel: React.FC<MentorIntelligencePanelProps> = ({ mentor }) => {
  return (
    <aside className="w-[350px] h-full shrink-0 flex flex-col gap-6 p-8 bg-white border-r border-stone-200 overflow-y-auto">
      <div className="flex flex-col gap-3">
        <div className="flex items-start justify-between">
          <h2 className="text-2xl font-bold text-primary-dark tracking-wide">{mentor.name}</h2>
          <StatusBadge status={mentor.status} />
        </div>
        <p className="text-sm text-stone-500">
          {mentor.university} / {mentor.school}
        </p>
      </div>

      <div className="p-5 bg-alt-bg rounded-2xl border border-stone-100 flex flex-col gap-1">
        <MentorContextItem label="职称" value={mentor.title} />
        <MentorContextItem label="匹配度" value={<span className="font-bold text-lg text-primary">{mentor.matchScore}</span>} />
        <MentorContextItem label="已发送信件" value={<span className="font-medium text-stone-600">{mentor.sentCount} 封</span>} />
      </div>

      <div>
        <SectionTitle label="研究方向情报" icon={<BrainCircuit className="w-4 h-4 text-primary" />} />
        <div className="flex flex-wrap gap-2.5">
          {mentor.research.map((res: string) => (
            <span
              key={res}
              className="flex items-center gap-1.5 text-xs px-3.5 py-1.5 rounded-full bg-red-50 text-primary-dark border border-red-100 shadow-sm"
            >
              {res}
            </span>
          ))}
        </div>
      </div>
    </aside>
  );
};
