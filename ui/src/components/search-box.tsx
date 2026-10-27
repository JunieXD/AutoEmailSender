import { cn } from "cn";
import { Search } from "lucide-react";
import { useEffect, useEffectEvent, useState } from "react";

/** A keyword kept in the URL, committed once typing pauses. */
export function SearchBox({
  value,
  onChange,
  label,
  placeholder,
  id,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  label: string;
  placeholder: string;
  id?: string;
  className?: string;
}) {
  const [text, setText] = useState(value);
  // Follow the URL when it changes from elsewhere, such as going back.
  const [shown, setShown] = useState(value);
  if (value !== shown) {
    setShown(value);
    setText(value);
  }
  const commit = useEffectEvent(onChange);
  useEffect(() => {
    const timer = setTimeout(() => {
      if (text.trim() !== value) commit(text.trim());
    }, 300);
    return () => clearTimeout(timer);
  }, [text, value]);
  return (
    <label
      className={cn(
        "flex h-10 items-center gap-2 rounded-full bg-muted px-4 text-muted-foreground focus-within:ring-2 focus-within:ring-ring",
        className,
      )}
    >
      <Search className="size-[18px] shrink-0" strokeWidth={1.6} />
      <input
        id={id}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={placeholder}
        aria-label={label}
        className="min-w-0 flex-1 bg-transparent text-foreground outline-none placeholder:text-faint"
      />
    </label>
  );
}
