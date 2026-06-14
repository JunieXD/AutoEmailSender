import { AtSign, ExternalLink, GraduationCap, Microscope, Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import { normalizeProfessorTitleDisplay } from "@/lib/professorTitle";
import type { WorkspaceThreadDTO } from "@/types";

type WorkspaceSidebarProps = {
  thread: WorkspaceThreadDTO;
};

const fieldClassName =
  "rounded-[22px] border border-stone-200/80 bg-white/88 px-4 py-3 shadow-[0_18px_34px_-30px_rgba(41,37,36,0.2)]";

const ArchiveField = ({
  icon,
  label,
  value,
}: {
  icon: ReactNode;
  label: string;
  value: ReactNode;
}) => (
  <div className={fieldClassName}>
    <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-stone-400">
      <span className="flex h-7 w-7 items-center justify-center rounded-xl bg-primary/8 text-primary">
        {icon}
      </span>
      {label}
    </div>
    <div className="mt-2 text-sm leading-6 text-stone-800">{value}</div>
  </div>
);

const ArchiveCard = ({ thread }: WorkspaceSidebarProps) => {
  const professor = thread.professor;
  const normalizedTitle = normalizeProfessorTitleDisplay(professor.title);
  const organization =
    [professor.university, professor.school].filter(Boolean).join(" / ") ||
    "未填写学校信息";
  const profileUrl = professor.profile_url?.trim();
  const openProfileUrl = (event: React.MouseEvent<HTMLAnchorElement>) => {
    if (!window.autoEmailSender?.openExternalUrl || !profileUrl) {
      return;
    }

    event.preventDefault();
    void window.autoEmailSender.openExternalUrl(profileUrl).catch(() => {
      window.open(profileUrl, "_blank", "noopener,noreferrer");
    });
  };

  return (
    <div className="space-y-3">
      <section className="overflow-hidden rounded-[30px] border border-stone-200 bg-[linear-gradient(180deg,rgba(255,250,242,0.98),rgba(255,255,255,0.98))] shadow-[0_20px_40px_-32px_rgba(41,37,36,0.25)]">
        <div className="border-b border-stone-200/80 px-5 py-5">
          <div className="flex items-start gap-3">
            <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-primary text-base font-semibold text-white shadow-sm shadow-primary/20">
              {professor.name.slice(0, 1)}
            </div>
            <div className="min-w-0">
              <div className="text-xs font-semibold uppercase tracking-[0.16em] text-stone-400">
                老师档案
              </div>
              <h2 className="mt-1 text-xl font-semibold tracking-[0.01em] text-stone-950">
                {professor.name}
              </h2>
              <p className="mt-1 text-sm text-stone-500">
                {normalizedTitle || "未填写职称"}
              </p>
            </div>
          </div>
        </div>

        <div className="space-y-3 px-4 py-4">
          <ArchiveField
            icon={<GraduationCap className="h-4 w-4" />}
            label="单位"
            value={organization}
          />
          <ArchiveField
            icon={<Microscope className="h-4 w-4" />}
            label="研究方向"
            value={professor.research_direction || "暂无研究方向信息"}
          />
          <ArchiveField
            icon={<AtSign className="h-4 w-4" />}
            label="邮箱"
            value={professor.email || "暂无邮箱"}
          />
          {profileUrl ? (
            <ArchiveField
              icon={<ExternalLink className="h-4 w-4" />}
              label="主页"
              value={
                <a
                  href={profileUrl}
                  target="_blank"
                  rel="noreferrer"
                  onClick={openProfileUrl}
                  className="inline-flex max-w-full items-center gap-1.5 text-primary underline-offset-4 hover:underline"
                >
                  <span className="truncate">{profileUrl}</span>
                </a>
              }
            />
          ) : null}
        </div>
      </section>
    </div>
  );
};

const AnalysisList = ({
  title,
  items,
  emptyText,
}: {
  title: string;
  items: string[];
  emptyText: string;
}) => (
  <div>
    <div className="text-xs font-semibold text-stone-500">{title}</div>
    {items.length > 0 ? (
      <ul className="mt-2 space-y-1.5">
        {items.map((item) => (
          <li key={item} className="flex gap-2 text-sm leading-6 text-stone-700">
            <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-primary/70" />
            <span className="min-w-0">{item}</span>
          </li>
        ))}
      </ul>
    ) : (
      <div className="mt-2 text-sm leading-6 text-stone-400">{emptyText}</div>
    )}
  </div>
);

const MatchAnalysisCard = ({ thread }: WorkspaceSidebarProps) => {
  const task = thread.current_task;
  const hasAnalysis =
    task.match_score !== null ||
    Boolean(task.match_reason?.trim()) ||
    task.fit_points.length > 0 ||
    task.risk_points.length > 0 ||
    task.match_keywords.length > 0;

  return (
    <section className="overflow-hidden rounded-[30px] border border-stone-200 bg-white/95 shadow-[0_20px_40px_-32px_rgba(41,37,36,0.25)]">
      <div className="flex items-center justify-between gap-3 border-b border-stone-200/80 px-5 py-4">
        <div className="flex min-w-0 items-center gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-primary/8 text-primary">
            <Sparkles className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <div className="text-xs font-semibold uppercase tracking-[0.16em] text-stone-400">
              匹配分析
            </div>
            <div className="mt-1 text-sm font-semibold text-stone-900">
              {hasAnalysis ? "分析结果" : "暂无匹配分析"}
            </div>
          </div>
        </div>
        {task.match_score !== null ? (
          <div className="shrink-0 rounded-2xl bg-primary px-3 py-2 text-sm font-semibold text-white">
            {task.match_score} 分
          </div>
        ) : null}
      </div>

      <div className="space-y-4 px-5 py-4">
        {hasAnalysis ? (
          <>
            <div>
              <div className="text-xs font-semibold text-stone-500">理由</div>
              <p className="mt-2 text-sm leading-6 text-stone-700">
                {task.match_reason?.trim() || "暂无匹配理由。"}
              </p>
            </div>
            <AnalysisList
              title="契合点"
              items={task.fit_points}
              emptyText="暂无契合点。"
            />
            <AnalysisList
              title="风险点"
              items={task.risk_points}
              emptyText="暂无风险点。"
            />
            <div>
              <div className="text-xs font-semibold text-stone-500">关键词</div>
              {task.match_keywords.length > 0 ? (
                <div className="mt-2 flex flex-wrap gap-2">
                  {task.match_keywords.map((keyword) => (
                    <span
                      key={keyword}
                      className="rounded-full border border-primary/15 bg-primary/6 px-2.5 py-1 text-xs font-medium text-primary"
                    >
                      {keyword}
                    </span>
                  ))}
                </div>
              ) : (
                <div className="mt-2 text-sm leading-6 text-stone-400">
                  暂无关键词。
                </div>
              )}
            </div>
          </>
        ) : (
          <p className="text-sm leading-6 text-stone-500">
            点击“分析匹配度”后，这里会显示分数、理由和建议。
          </p>
        )}
      </div>
    </section>
  );
};

export const WorkspaceSidebar = (props: WorkspaceSidebarProps) => (
  <>
    <div className="lg:hidden">
      <ArchiveCard {...props} />
      <div className="mt-3">
        <MatchAnalysisCard {...props} />
      </div>
    </div>

    <aside className="hidden lg:block">
      <div className="sticky top-0 space-y-3">
        <ArchiveCard {...props} />
        <MatchAnalysisCard {...props} />
      </div>
    </aside>
  </>
);
