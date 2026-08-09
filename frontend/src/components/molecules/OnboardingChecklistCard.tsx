import { CheckCircle2, Circle } from 'lucide-react';
import { Link } from 'react-router-dom';
import clsx from 'clsx';

interface OnboardingChecklistItem {
  label: string;
  done: boolean;
}

interface OnboardingChecklistCardProps {
  title: string;
  description: string;
  nextActionHref: string;
  nextActionLabel: string;
  items: OnboardingChecklistItem[];
}

export const OnboardingChecklistCard = ({
  title,
  description,
  nextActionHref,
  nextActionLabel,
  items,
}: OnboardingChecklistCardProps) => (
  <section
    data-testid="onboarding-checklist-card"
    className="rounded-3xl border border-stone-200 bg-[#fcfbf8] p-6 shadow-sm sm:p-8"
  >
    <div className="max-w-3xl">
      <div className="inline-flex rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-medium text-amber-800">
        新手引导
      </div>
      <h1 className="mt-4 text-3xl font-semibold text-stone-900">{title}</h1>
      <p className="mt-3 text-sm leading-6 text-stone-600">{description}</p>
    </div>

    <div className="mt-6 grid gap-3 sm:grid-cols-2">
      {items.map((item) => (
        <div
          key={item.label}
          className="flex items-center gap-3 rounded-2xl border border-stone-200 bg-white px-4 py-3 text-sm shadow-sm"
        >
          {item.done ? (
            <CheckCircle2 className="h-5 w-5 text-emerald-600" />
          ) : (
            <Circle className="h-5 w-5 text-stone-400" />
          )}
          <span className={clsx('font-medium', item.done ? 'text-stone-900' : 'text-stone-700')}>
            {item.label}
          </span>
        </div>
      ))}
    </div>

    <div className="mt-6">
      <Link to={nextActionHref} data-interactive="button" className="ui-btn-primary">
        {nextActionLabel}
      </Link>
    </div>
  </section>
);
