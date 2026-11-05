import { type MutationState, useMutation, useMutationState, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Check, ChevronDown } from "lucide-react";
import { type ReactNode, useRef, useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { Mailbox, Material, TestLetter } from "@/api/types";
import { Attachments } from "@/components/attachments";
import { openLinks } from "@/components/correspondence";
import { ImportDialog } from "@/components/import-dialog";
import { MailboxDialog, useProviders } from "@/components/mailbox-dialog";
import { EMPTY_FORM, MentorDialog } from "@/components/mentor-dialog";
import { ModelDialog, useModelProviders } from "@/components/model-dialog";
import { TemplateDialog, usePlaceholders } from "@/components/template-dialog";
import { Button, buttonVariants } from "@/components/ui/button";
import { letterHtml, letterText } from "@/letter";
import { uploadMaterial } from "@/material";
import { findSearch } from "./find";
import { ProfileFields } from "./profile";

/** What 写模板 starts from when there is no template yet (docs/ui.md#交互). */
const SAMPLE_TEMPLATE = {
  name: "首封",
  subject: "报考{导师姓名}老师研究生的咨询",
  bodyMd:
    "{导师姓名}老师：\n\n您好！我是{我的姓名}，一直关注您在{学校}的研究工作，希望有机会报考您的研究生，加入您的课题组。\n\n随信附上我的简历，请您审阅。期待您的回复！\n\n此致\n敬礼\n\n{我的姓名}",
};

const SEND_TEST_LETTER = ["sendTestLetter"];

/**
 * The first-run guide (docs/ui.md#交互). Whether a step is done is read from the data; the test
 * letter, which leaves none, counts as done once sent in this run of the app.
 */
export function WelcomePage() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const profile = useQuery({ queryKey: ["getProfile"], queryFn: () => call("getProfile", {}) });
  const mailboxes = useQuery({ queryKey: ["listMailboxes"], queryFn: () => call("listMailboxes", {}) });
  const models = useQuery({ queryKey: ["listModelConfigs"], queryFn: () => call("listModelConfigs", {}) });
  const materials = useQuery({ queryKey: ["listMaterials"], queryFn: () => call("listMaterials", {}) });
  const templates = useQuery({ queryKey: ["listTemplates"], queryFn: () => call("listTemplates", {}) });
  const mentors = useQuery({ queryKey: ["listMentors", {}], queryFn: () => call("listMentors", { page: 1 }) });
  const tasks = useQuery({ queryKey: ["listTasks", 1], queryFn: () => call("listTasks", { page: 1 }) });
  const sent = useMutationState<MutationState<null, Error, { to: string }>>({
    filters: { mutationKey: SEND_TEST_LETTER, status: "success" },
    select: (m) => m.state,
  }).at(-1)?.variables?.to;
  const finish = useMutation({
    scope: { id: "updateProfile" },
    mutationFn: () => call("finishOnboarding", {}),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["getProfile"] });
      await navigate({ to: "/mentors" });
    },
  });
  const error =
    profile.error ??
    mailboxes.error ??
    models.error ??
    materials.error ??
    templates.error ??
    mentors.error ??
    tasks.error;
  if (error) return <p className="px-7 py-6 text-sm text-destructive">未能加载：{errorMessage(error)}</p>;
  if (
    !profile.data ||
    !mailboxes.data ||
    !models.data ||
    !materials.data ||
    !templates.data ||
    !mentors.data ||
    !tasks.data
  )
    return null;
  const defaultTemplate = templates.data.find((t) => t.isDefault) ?? templates.data[0];
  const steps: Step[] = [
    {
      title: "你的信息",
      done: profile.data.name !== null,
      result: profile.data.name,
      intro: "AI 写信时用你的姓名落款，分析匹配度时参考你的意向研究方向。",
      body: <ProfileFields basic />,
    },
    {
      title: "发信邮箱",
      done: mailboxes.data.length > 0,
      result: mailboxes.data.map((m) => m.address).join("、"),
      intro: "给导师发信、收导师的回信都用它。QQ、163 这类邮箱要用授权码登录，添加时会写明怎么获取。",
      action: <AddMailbox />,
    },
    {
      title: "AI 模型",
      done: models.data.length > 0,
      result: models.data.map((m) => m.name ?? m.model).join("、"),
      intro: "分析匹配度、起草和改写邮件要用它。不用 AI 也能套模板写信，可以先跳过。",
      action: <AddModel />,
    },
    {
      title: "材料",
      done: materials.data.length > 0,
      result: `${materials.data.length} 份：${materials.data.map((m) => m.fileName).join("、")}`,
      intro: "简历、成绩单这类材料。AI 写信和分析匹配度会参考其中的文字，也可以作为附件随信发出。",
      action: <AddMaterial />,
    },
    {
      title: "模板",
      done: templates.data.length > 0,
      result: defaultTemplate?.name,
      intro: "首封邮件的底稿。用上导师姓名这类占位符，写给每位导师时自动换成导师的资料。",
      action: <AddTemplate first={templates.data.length === 0} />,
    },
    {
      title: "试发一封",
      done: sent !== undefined,
      result: sent && `已发到 ${sent}`,
      intro: "把模板套上一位示例导师，带上默认附件，发到你自己的邮箱，看看导师收到的是什么样。",
      body: <TestLetterStep mailboxes={mailboxes.data} materials={materials.data} hasTemplate={!!defaultTemplate} />,
    },
    {
      title: "添加导师",
      done: mentors.data.total > 0,
      result: `${mentors.data.total} 位导师`,
      intro: "从导师库挑选，从学院官网智能整理，导入表格，或者逐位手动添加。",
      action: <AddMentors />,
    },
    {
      title: "第一个任务",
      done: tasks.data.total > 0,
      result: `${tasks.data.total} 个任务`,
      intro: "在导师列表勾选要联系的导师，点“新建任务”，为每位导师写好首封邮件，审核后发出。",
      action: (
        <Link to="/mentors" className={buttonVariants()}>
          去导师列表
        </Link>
      ),
    },
  ];
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="flex max-w-[760px] flex-col px-7 pt-6 pb-10 phone:px-4">
        <header className="flex items-start gap-4 pb-6">
          <div className="grow">
            <h1 className="text-2xl font-semibold">开始使用</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              配好下面几项，就可以开始联系导师。每一步都能先跳过，以后在设置里改。
            </p>
          </div>
          <Button variant="quiet" disabled={finish.isPending} onClick={() => finish.mutate()}>
            跳过引导
          </Button>
        </header>
        <Steps steps={steps} />
        <div className="mt-6 flex items-center justify-end gap-3">
          {finish.error && <p className="text-sm text-destructive">{errorMessage(finish.error)}</p>}
          <Button disabled={finish.isPending} onClick={() => finish.mutate()}>
            完成
          </Button>
        </div>
      </div>
    </div>
  );
}

function Steps({ steps }: { steps: Step[] }) {
  // The first step not done when the page opens stays open while it is being done.
  const [open, setOpen] = useState(() => steps.findIndex((s) => !s.done));
  return (
    <ol className="divide-y divide-border border-y border-border">
      {steps.map((s, i) => (
        <StepRow
          key={s.title}
          step={s}
          number={i + 1}
          open={open === i}
          onToggle={() => setOpen(open === i ? -1 : i)}
          onNext={i + 1 < steps.length ? () => setOpen(i + 1) : undefined}
        />
      ))}
    </ol>
  );
}

type Step = {
  title: string;
  done: boolean;
  /** What doing the step gave, shown beside it once done. */
  result?: string | null;
  intro: string;
  body?: ReactNode;
  action?: ReactNode;
};

function StepRow({
  step: s,
  number,
  open,
  onToggle,
  onNext,
}: {
  step: Step;
  number: number;
  open: boolean;
  onToggle: () => void;
  onNext?: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        aria-expanded={open}
        className="group flex min-h-14 w-full items-center gap-3 px-1 py-3 text-left"
        onClick={onToggle}
      >
        <span
          className={`grid size-7 shrink-0 place-items-center rounded-full text-sm ${
            s.done ? "bg-success text-white" : "border border-input text-muted-foreground"
          }`}
        >
          {s.done ? <Check className="size-4" strokeWidth={2.2} aria-label="已完成" /> : number}
        </span>
        <span className="shrink-0 font-medium">{s.title}</span>
        {s.done && s.result && <span className="min-w-0 truncate text-sm text-muted-foreground">{s.result}</span>}
        <ChevronDown
          className={`ml-auto size-[18px] shrink-0 text-muted-foreground transition-transform group-hover:text-foreground ${open ? "rotate-180" : ""}`}
          strokeWidth={1.6}
        />
      </button>
      {open && (
        <div className="flex flex-col gap-4 pr-1 pb-6 pl-11 phone:pl-1">
          <p className="text-sm text-muted-foreground">{s.intro}</p>
          {s.body}
          {(s.action ?? onNext) && (
            <div className="flex flex-wrap items-center gap-2">
              {s.action}
              {onNext && (
                <Button variant="quiet" onClick={onNext}>
                  下一步
                </Button>
              )}
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function AddMailbox() {
  const providers = useProviders();
  const [adding, setAdding] = useState(false);
  return (
    <>
      <Button disabled={!providers.data} onClick={() => setAdding(true)}>
        添加邮箱
      </Button>
      {providers.error && <p className="text-sm text-destructive">{errorMessage(providers.error)}</p>}
      {adding && providers.data && (
        <MailboxDialog mailbox={null} providers={providers.data} onClose={() => setAdding(false)} />
      )}
    </>
  );
}

function AddModel() {
  const providers = useModelProviders();
  const [adding, setAdding] = useState(false);
  return (
    <>
      <Button disabled={!providers.data} onClick={() => setAdding(true)}>
        添加模型
      </Button>
      {providers.error && <p className="text-sm text-destructive">{errorMessage(providers.error)}</p>}
      {adding && providers.data && (
        <ModelDialog config={null} providers={providers.data} onClose={() => setAdding(false)} />
      )}
    </>
  );
}

function AddMaterial() {
  const queryClient = useQueryClient();
  const picker = useRef<HTMLInputElement>(null);
  const add = useMutation({
    mutationFn: uploadMaterial,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["listMaterials"] }),
  });
  return (
    <>
      <input
        ref={picker}
        type="file"
        accept=".pdf,.docx,.doc"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) add.mutate(file);
        }}
      />
      <Button disabled={add.isPending} onClick={() => picker.current?.click()}>
        {add.isPending ? "正在提取文字…" : "添加材料"}
      </Button>
      <Link to="/settings/materials" className={buttonVariants({ variant: "ghost" })}>
        设置用途
      </Link>
      {add.error && <p className="w-full text-sm text-destructive">{errorMessage(add.error)}</p>}
    </>
  );
}

function AddTemplate({ first }: { first: boolean }) {
  const placeholders = usePlaceholders();
  const [adding, setAdding] = useState(false);
  return (
    <>
      <Button disabled={!placeholders.data} onClick={() => setAdding(true)}>
        写模板
      </Button>
      {placeholders.error && <p className="text-sm text-destructive">{errorMessage(placeholders.error)}</p>}
      {adding && placeholders.data && (
        <TemplateDialog
          template={null}
          sample={first ? SAMPLE_TEMPLATE : undefined}
          placeholders={placeholders.data}
          onClose={() => setAdding(false)}
        />
      )}
    </>
  );
}

function TestLetterStep({
  mailboxes,
  materials,
  hasTemplate,
}: {
  mailboxes: Mailbox[];
  materials: Material[];
  hasTemplate: boolean;
}) {
  const mailbox = mailboxes.at(0);
  if (!mailbox || !hasTemplate)
    return (
      <p className="text-sm">
        {!mailbox && !hasTemplate ? "先添加发信邮箱，再写模板。" : !mailbox ? "先添加发信邮箱。" : "先写模板。"}
      </p>
    );
  return <TestLetterPreview mailbox={mailbox} materials={materials} />;
}

function TestLetterPreview({ mailbox, materials }: { mailbox: Mailbox; materials: Material[] }) {
  const letter = useQuery({ queryKey: ["testLetter"], queryFn: () => call("testLetter", {}) });
  const style = useQuery({ queryKey: ["getLetterStyle"], queryFn: () => call("getLetterStyle", {}) });
  const send = useMutation({
    mutationKey: SEND_TEST_LETTER,
    // Kept for the rest of this run of the app: the step counts as done once sent.
    gcTime: Infinity,
    mutationFn: ({ letter: l }: { to: string; letter: TestLetter }) => {
      if (!style.data) throw new Error("信件样式尚未加载，请稍后再试。");
      return call("sendTestLetter", {
        mailboxId: mailbox.id,
        subject: l.subject,
        html: letterHtml(l.bodyMd, style.data),
        text: letterText(l.bodyMd),
        attachments: l.attachments,
      });
    },
  });
  // Closing the step and opening it again must not allow a second letter while the first is on its way.
  const last = useMutationState({ filters: { mutationKey: SEND_TEST_LETTER }, select: (m) => m.state }).at(-1);
  const error = letter.error ?? style.error;
  if (error) return <p className="text-sm text-destructive">未能准备试发的邮件：{errorMessage(error)}</p>;
  if (!letter.data || !style.data) return null;
  const l = letter.data;
  return (
    <div className="flex flex-col gap-3">
      {/* On white whatever the app's theme, as mail apps show letters. */}
      <article className="rounded-lg border border-border bg-white px-6 py-5 text-black">
        <h3 className="mb-3 font-semibold break-words">{l.subject || "（无主题）"}</h3>
        <div onClick={openLinks} dangerouslySetInnerHTML={{ __html: letterHtml(l.bodyMd, style.data) }} />
      </article>
      <Attachments files={materials.filter((m) => l.attachments.includes(m.id))} />
      <p className="text-sm text-muted-foreground">
        用的是模板“{l.templateName}”，示例导师是示例大学计算机学院的张明教授。
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <Button disabled={last?.status === "pending"} onClick={() => send.mutate({ to: mailbox.address, letter: l })}>
          {last?.status === "pending" ? "正在发送…" : `发给自己（${mailbox.address}）`}
        </Button>
        {last?.status === "success" && <p className="text-sm text-success">已发出，去邮箱的收件箱看看。</p>}
        {last?.error && <p className="text-sm text-destructive">未能发出：{errorMessage(last.error)}</p>}
      </div>
    </div>
  );
}

function AddMentors() {
  const queryClient = useQueryClient();
  const [importing, setImporting] = useState(false);
  const [adding, setAdding] = useState(false);
  return (
    <>
      <Link to="/find" search={findSearch({ tab: "library" })} className={buttonVariants()}>
        从导师库添加
      </Link>
      <Link to="/find" search={findSearch({ tab: "crawl" })} className={buttonVariants({ variant: "quiet" })}>
        智能整理
      </Link>
      <Button variant="quiet" onClick={() => setImporting(true)}>
        导入表格
      </Button>
      <Button variant="quiet" onClick={() => setAdding(true)}>
        手动添加
      </Button>
      {importing && <ImportDialog onClose={() => setImporting(false)} onImported={() => {}} />}
      {adding && (
        <MentorDialog
          title="添加导师"
          action="添加"
          initial={EMPTY_FORM}
          save={(form) => call("addMentor", form)}
          onSaved={async () => {
            await queryClient.invalidateQueries({ queryKey: ["listMentors"] });
            setAdding(false);
          }}
          onClose={() => setAdding(false)}
        />
      )}
    </>
  );
}
