import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useCanGoBack, useParams, useRouter } from "@tanstack/react-router";
import { openUrl } from "@tauri-apps/plugin-opener";
import { ChevronLeft, UserRound } from "lucide-react";
import { type ReactNode, useEffect, useEffectEvent, useRef, useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { MentorDetail } from "@/api/types";
import { Avatar, ContactLabel, contactDetail, MATCH_LABEL, Missing, TagChip } from "@/components/mentor";
import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { day } from "@/format";

/** One mentor: the correspondence in the middle, the profile and match analysis on the right (docs/ui.md#页面). */
export function MentorPage() {
  const { mentorId } = useParams({ from: "/mentors/$mentorId" });
  const query = useQuery({
    queryKey: ["getMentor", mentorId],
    queryFn: () => call("getMentor", { id: mentorId }),
  });
  // On a phone the profile replaces the correspondence instead of sitting beside it.
  const [profile, setProfile] = useState(false);
  if (query.isPending) return null;
  if (query.isError) {
    return (
      <Notice title="导师资料未能加载" detail={errorMessage(query.error)}>
        <Link to="/mentors" className="mt-4 inline-block text-sm text-brand-text">
          返回导师列表
        </Link>
      </Notice>
    );
  }
  const m = query.data;
  return (
    <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_340px] phone:grid-cols-1">
      <section className={`flex min-h-0 min-w-0 flex-col ${profile ? "phone:hidden" : ""}`}>
        <Header mentor={m} onProfile={() => setProfile(true)} />
        <div className="grid flex-1 place-items-center p-6 text-center text-sm text-muted-foreground">
          {m.contact.status === "none" ? `尚未联系${m.name}老师。` : contactDetail(m.contact)}
        </div>
      </section>
      <aside
        className={`flex min-h-0 flex-col gap-[18px] overflow-y-auto px-5 py-[22px] text-sm ${profile ? "" : "phone:hidden"}`}
      >
        <Button variant="ghost" className="hidden self-start px-2 phone:inline-flex" onClick={() => setProfile(false)}>
          <ChevronLeft strokeWidth={1.6} />
          返回往来
        </Button>
        <Profile mentor={m} />
        <Analysis mentor={m} />
        {m.papers.length > 0 && (
          <Section title="代表论文">
            <ol className="flex list-decimal flex-col gap-1.5 pl-[1.1em] text-muted-foreground">
              {m.papers.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ol>
          </Section>
        )}
        <Section title="备注">
          <Note key={m.id} id={m.id} note={m.note} />
        </Section>
      </aside>
    </div>
  );
}

function Header({ mentor: m, onProfile }: { mentor: MentorDetail; onProfile: () => void }) {
  const router = useRouter();
  const canGoBack = useCanGoBack();
  return (
    <header className="flex items-center gap-3 border-b border-border px-5 py-3.5 phone:px-3">
      <Button
        variant="ghost"
        size="icon"
        aria-label="返回导师列表"
        // Going back keeps the list's filters and page.
        onClick={() => (canGoBack ? router.history.back() : router.navigate({ to: "/mentors" }))}
      >
        <ChevronLeft strokeWidth={1.6} />
      </Button>
      <Avatar id={m.id} name={m.name} />
      <div className="min-w-0 grow">
        <h1 className="truncate text-lg font-semibold">
          {m.name}
          {m.title && <span className="ml-1.5 text-sm font-normal text-muted-foreground">{m.title}</span>}
        </h1>
        <p className="truncate text-sm text-muted-foreground">
          {[m.university, m.school, m.department].filter(Boolean).join(" ") || <Missing>未填写学校</Missing>}
        </p>
      </div>
      <ContactLabel contact={m.contact} className="text-sm phone:hidden" />
      <div className="flex gap-1 narrow:hidden">
        {m.tags.map((t) => (
          <TagChip key={t.name} tag={t} />
        ))}
      </div>
      <Button variant="ghost" className="hidden px-3 phone:inline-flex" onClick={onProfile}>
        <UserRound strokeWidth={1.6} />
        资料
      </Button>
    </header>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h2 className="mb-2 font-medium text-muted-foreground">{title}</h2>
      {children}
    </section>
  );
}

function Profile({ mentor: m }: { mentor: MentorDetail }) {
  return (
    <Section title="资料">
      <dl className="grid grid-cols-[52px_minmax(0,1fr)] gap-x-3 gap-y-2 [&_dd]:wrap-anywhere [&_dt]:text-muted-foreground">
        <dt>邮箱</dt>
        <dd>{m.email ?? <Missing>未填写</Missing>}</dd>
        <dt>主页</dt>
        <dd>{m.homepageUrl ? <ExternalLink url={m.homepageUrl} /> : <Missing>未填写</Missing>}</dd>
        <dt>研究方向</dt>
        <dd>{m.research ?? <Missing>未填写</Missing>}</dd>
        {m.sourceUrl && (
          <>
            <dt>来源</dt>
            <dd>
              <ExternalLink url={m.sourceUrl} />
            </dd>
          </>
        )}
      </dl>
    </Section>
  );
}

/** Opens in the system browser. Addresses come from web pages and imports, so only web links are links. */
function ExternalLink({ url }: { url: string }) {
  if (!/^https?:\/\//i.test(url)) return <>{url}</>;
  return (
    <a
      href={url}
      className="text-brand-text hover:underline"
      onClick={(e) => {
        e.preventDefault();
        void openUrl(url);
      }}
    >
      {url}
    </a>
  );
}

function Analysis({ mentor: m }: { mentor: MentorDetail }) {
  const a = m.analysis;
  if (a.state !== "done") {
    const [label, color] = MATCH_LABEL[a.state];
    return (
      <Section title="匹配度">
        <p className={color}>{a.state === "failed" ? `${label}：${a.error}` : label}</p>
      </Section>
    );
  }
  return (
    <>
      <Section title="匹配度">
        <p className="text-[32px] leading-none font-semibold">
          {a.score}
          <span className="ml-1.5 text-sm font-normal text-muted-foreground">{day(a.matchedAt)}分析</span>
        </p>
        <p className="mt-2.5 text-muted-foreground">{a.reason}</p>
      </Section>
      <Points title="契合方向" items={a.directions} />
      <Points title="建议强调" items={a.highlights} />
      <Points title="风险" items={a.risks} marker="marker:text-warning" />
    </>
  );
}

function Points({ title, items, marker = "" }: { title: string; items: string[]; marker?: string }) {
  if (items.length === 0) return null;
  return (
    <Section title={title}>
      <ul className={`flex list-disc flex-col gap-1.5 pl-[1.1em] ${marker}`}>
        {items.map((p) => (
          <li key={p}>{p}</li>
        ))}
      </ul>
    </Section>
  );
}

/** Saves as the user pauses typing and when they leave, one save at a time so the last text wins. */
function Note({ id, note }: { id: number; note: string | null }) {
  const queryClient = useQueryClient();
  const [text, setText] = useState(note ?? "");
  const [error, setError] = useState<string>();
  const saved = useRef<string | null>(note ?? "");
  const saving = useRef(Promise.resolve());
  function save(value: string) {
    if (value === saved.current) return;
    saved.current = value;
    saving.current = saving.current
      .then(() => call("setMentorNote", { id, note: value }))
      .then(
        () => {
          setError(undefined);
          // The page may be opened again from the cache before it reloads.
          queryClient.setQueryData<MentorDetail>(
            ["getMentor", id],
            (m) => m && { ...m, note: value.trim() ? value : null },
          );
        },
        (e: unknown) => {
          // Unsaved again, so the next pause or leaving retries.
          saved.current = null;
          setError(`备注未能保存：${errorMessage(e)}`);
        },
      );
  }
  const saveLater = useEffectEvent(save);
  const saveNow = useEffectEvent(() => save(text));
  useEffect(() => {
    const timer = setTimeout(() => saveLater(text), 600);
    return () => clearTimeout(timer);
  }, [text]);
  useEffect(() => () => saveNow(), []);
  return (
    <>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => save(text)}
        aria-label="备注"
        placeholder="仅自己可见"
        className="block min-h-16 w-full resize-y rounded-md bg-muted px-3 py-2.5 leading-relaxed outline-none placeholder:text-faint focus:ring-[1.5px] focus:ring-primary focus:ring-inset"
      />
      {error && <p className="mt-1.5 text-destructive">{error}</p>}
    </>
  );
}
