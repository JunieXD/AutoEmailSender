import clsx from 'clsx';
import { Search } from 'lucide-react';

interface SearchInputProps {
  value: string;
  onChange: (val: string) => void;
  placeholder?: string;
  ariaLabel?: string;
  variant?: 'compact' | 'fluid';
  className?: string;
}

export const SearchInput: React.FC<SearchInputProps> = ({ 
  value, 
  onChange, 
  placeholder = "搜索姓名、学校…",
  ariaLabel = "搜索",
  variant = 'compact',
  className,
}) => {
  return (
    <div className={clsx('relative flex items-center', className)}>
      <Search className="pointer-events-none absolute left-3 h-4 w-4 text-stone-400" />
      <input
        type="search"
        aria-label={ariaLabel}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={clsx(
          'text-sm',
          variant === 'fluid'
            ? 'form-input w-full pl-9 pr-4'
            : 'w-48 rounded-full border border-stone-200 py-2 pl-9 pr-4 transition-shadow focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary',
        )}
      />
    </div>
  );
};
