import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { openUrl } from "@tauri-apps/plugin-opener";
import { ChevronRight } from "lucide-react";
import { useRef, useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { Mailbox, MailboxTest, NewMailbox, Provider, Security, Server } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Field, TestResult } from "@/components/form";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Dialog, DialogBody, DialogClose, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";

/** The provider table from the core; it never changes while the app runs. */
export function useProviders() {
  return useQuery({ queryKey: ["listProviders"], queryFn: () => call("listProviders", {}), staleTime: Infinity });
}

const CUSTOM = "custom";

const BLANK_SERVERS: Pick<NewMailbox, "smtp" | "imap"> = {
  smtp: { host: "", port: 465, security: "tls" },
  imap: { host: "", port: 993, security: "tls" },
};

// docs/ui.md#交互: a new mailbox starts at 50 emails a day, at least a minute apart.
const EMPTY: NewMailbox = {
  address: "",
  senderName: "",
  provider: "",
  ...BLANK_SERVERS,
  dailyLimit: 50,
  minIntervalS: 60,
  secret: "",
};

const U16 = 65535;
const U32 = 4294967295;

function sameHost(a: string, b: string): boolean {
  return a.trim().toLowerCase() === b.trim().toLowerCase();
}

/** Whether a number field holds a whole number in range; the core cannot even read others. */
function whole(n: number, min: number, max: number): boolean {
  return Number.isInteger(n) && n >= min && n <= max;
}

function formOf(m: Mailbox): NewMailbox {
  return {
    address: m.address,
    senderName: m.senderName,
    provider: m.provider,
    smtp: m.smtp,
    imap: m.imap,
    dailyLimit: m.dailyLimit,
    minIntervalS: m.minIntervalS,
    secret: "",
  };
}

/** The provider that serves the address's domain. */
function byDomain(providers: Provider[], address: string): Provider | undefined {
  const domain = address
    .slice(address.lastIndexOf("@") + 1)
    .trim()
    .toLowerCase();
  return address.includes("@") ? providers.find((p) => p.domains.includes(domain)) : undefined;
}

/** Switches the form to a provider, with its servers. */
function withProvider(form: NewMailbox, p: Provider): NewMailbox {
  return p.setup.kind === "password"
    ? { ...form, provider: p.id, smtp: p.setup.smtp, imap: p.setup.imap }
    : { ...form, provider: p.id };
}

/**
 * Adding or editing a mailbox (docs/ui.md#交互). The provider follows the address; addresses no
 * provider serves pick a hosted one or fill in the servers. Mounted while open.
 */
export function MailboxDialog({
  mailbox,
  providers,
  onSaved,
  onClose,
}: {
  mailbox: Mailbox | null;
  providers: Provider[];
  /** After saving, before closing. */
  onSaved?: () => void;
  onClose: () => void;
}) {
  const [initial] = useState(() => (mailbox ? formOf(mailbox) : EMPTY));
  const [form, setForm] = useState(initial);
  const [serversOpen, setServersOpen] = useState(initial.provider === CUSTOM);
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: (f: NewMailbox) => (mailbox ? call("updateMailbox", { ...f, id: mailbox.id }) : call("addMailbox", f)),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["listMailboxes"] });
      onSaved?.();
      onClose();
    },
  });
  const sending = useMutation({ mutationFn: (t: MailboxTest) => call("testSending", t) });
  const receiving = useMutation({ mutationFn: (t: MailboxTest) => call("testReceiving", t) });
  const addressRef = useRef<HTMLInputElement>(null);

  const provider = providers.find((p) => p.id === form.provider);
  const setup = provider?.setup;
  const usable = form.provider === CUSTOM || setup?.kind === "password";
  // The core only uses the saved secret with the servers it was saved for, as it compares them.
  const secretSaved =
    mailbox !== null && sameHost(form.smtp.host, mailbox.smtp.host) && sameHost(form.imap.host, mailbox.imap.host);
  const dirty = JSON.stringify(form) !== JSON.stringify(initial);
  const numbers =
    whole(form.dailyLimit, 1, U32) &&
    whole(form.minIntervalS, 0, U32) &&
    whole(form.smtp.port, 1, U16) &&
    whole(form.imap.port, 1, U16);
  const complete =
    usable &&
    numbers &&
    form.address.trim() !== "" &&
    form.senderName.trim() !== "" &&
    (secretSaved || form.secret.trim() !== "");

  /** Changes the form; earlier test results no longer apply. */
  function edit(next: NewMailbox) {
    setForm(next);
    sending.reset();
    receiving.reset();
  }

  function editAddress(address: string) {
    const found = byDomain(providers, address);
    if (found) {
      edit(found.id === form.provider ? { ...form, address } : withProvider({ ...form, address }, found));
    } else if (provider?.domains.length) {
      // The address has left the provider that served it.
      edit({ ...form, address, provider: "", ...BLANK_SERVERS });
    } else {
      edit({ ...form, address });
    }
  }

  function test() {
    const input = { ...form, id: mailbox?.id };
    sending.mutate(input);
    receiving.mutate(input);
  }

  const pending = save.isPending || sending.isPending || receiving.isPending;
  return (
    <Dialog
      open
      onOpenChange={(open, details) => {
        // docs/development.md#界面: what the user typed is not thrown away by a stray click or Esc,
        // nor while it is being saved.
        const stray = details.reason === "outside-press" || details.reason === "escape-key";
        if (!open && !save.isPending && !(dirty && stray)) onClose();
      }}
    >
      <DialogContent initialFocus={addressRef}>
        <form
          className="flex min-h-0 flex-1 flex-col"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate(form);
          }}
        >
          <DialogHeader>{mailbox ? "编辑邮箱" : "添加邮箱"}</DialogHeader>
          <DialogBody>
            <fieldset disabled={save.isPending} className="flex min-w-0 flex-col gap-3.5">
              <Field label="邮箱地址" id="mailbox-address">
                <Input
                  ref={addressRef}
                  id="mailbox-address"
                  type="email"
                  autoComplete="off"
                  spellCheck={false}
                  value={form.address}
                  onChange={(e) => editAddress(e.target.value)}
                />
              </Field>
              <ProviderField
                form={form}
                providers={providers}
                onChange={(p) => {
                  if (p === CUSTOM) setServersOpen(true);
                  edit(p === CUSTOM ? { ...form, provider: CUSTOM } : withProvider(form, p));
                }}
              />
              {setup?.kind === "unsupported" ? (
                <p className="rounded-md bg-muted px-3.5 py-3 text-sm">{setup.reason}</p>
              ) : (
                <>
                  <Field label="发件人名称" id="mailbox-sender">
                    <Input
                      id="mailbox-sender"
                      autoComplete="off"
                      value={form.senderName}
                      onChange={(e) => edit({ ...form, senderName: e.target.value })}
                    />
                  </Field>
                  <Field label={setup?.kind === "password" ? setup.secret : "密码或授权码"} id="mailbox-secret">
                    <Input
                      id="mailbox-secret"
                      type="password"
                      autoComplete="off"
                      placeholder={secretSaved ? "不修改就留空" : undefined}
                      value={form.secret}
                      onChange={(e) => edit({ ...form, secret: e.target.value })}
                    />
                  </Field>
                  {setup?.kind === "password" && (
                    <div className="rounded-md bg-muted px-3.5 py-3 text-sm">
                      <p className="font-medium">怎么获取{setup.secret}</p>
                      <ol className="mt-1.5 list-decimal space-y-1 pl-5 text-muted-foreground">
                        {setup.steps.map((s) => (
                          <li key={s}>{s}</li>
                        ))}
                      </ol>
                      <button
                        type="button"
                        className="mt-2 text-brand-text hover:underline"
                        onClick={() => void openUrl(setup.url)}
                      >
                        打开网页
                      </button>
                    </div>
                  )}
                  <Field label="发信限制" id="mailbox-daily">
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      每天最多发
                      <Input
                        id="mailbox-daily"
                        type="number"
                        min={1}
                        className="w-16"
                        aria-invalid={!whole(form.dailyLimit, 1, U32)}
                        value={Number.isNaN(form.dailyLimit) ? "" : form.dailyLimit}
                        onChange={(e) => edit({ ...form, dailyLimit: e.target.valueAsNumber })}
                      />
                      封，两封至少隔
                      <Input
                        aria-label="两封之间至少隔几秒"
                        type="number"
                        min={0}
                        className="w-16"
                        aria-invalid={!whole(form.minIntervalS, 0, U32)}
                        value={Number.isNaN(form.minIntervalS) ? "" : form.minIntervalS}
                        onChange={(e) => edit({ ...form, minIntervalS: e.target.valueAsNumber })}
                      />
                      秒
                    </div>
                  </Field>
                  <Collapsible open={serversOpen} onOpenChange={setServersOpen}>
                    <CollapsibleTrigger className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground [&[data-panel-open]>svg]:rotate-90">
                      <ChevronRight className="size-4 transition-transform" strokeWidth={1.6} />
                      服务器设置
                    </CollapsibleTrigger>
                    <CollapsibleContent className="mt-3 flex flex-col gap-3.5">
                      <ServerFields
                        label="发信（SMTP）"
                        id="mailbox-smtp"
                        server={form.smtp}
                        onChange={(smtp) => edit({ ...form, smtp })}
                      />
                      <ServerFields
                        label="收信（IMAP）"
                        id="mailbox-imap"
                        server={form.imap}
                        onChange={(imap) => edit({ ...form, imap })}
                      />
                    </CollapsibleContent>
                  </Collapsible>
                </>
              )}
            </fieldset>
          </DialogBody>
          {(sending.status !== "idle" || receiving.status !== "idle") && (
            <div className="flex flex-col gap-1.5 px-6 pt-3 text-sm phone:px-4" aria-live="polite">
              <TestResult label="发信" mutation={sending} />
              <TestResult label="收信" mutation={receiving} />
            </div>
          )}
          <DialogFooter>
            <p className="grow text-sm text-destructive" role="alert">
              {save.isError && errorMessage(save.error)}
            </p>
            <Button type="button" variant="quiet" disabled={pending || !complete} onClick={test}>
              测试连接
            </Button>
            <DialogClose render={<Button variant="ghost" />}>取消</DialogClose>
            <Button type="submit" disabled={save.isPending || !complete}>
              {mailbox ? "保存" : "添加"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** The recognised provider, or a choice of hosted ones and filling in the servers. */
function ProviderField({
  form,
  providers,
  onChange,
}: {
  form: NewMailbox;
  providers: Provider[];
  onChange: (provider: Provider | typeof CUSTOM) => void;
}) {
  const recognised = providers.find((p) => p.id === form.provider && p.domains.length > 0);
  if (recognised) {
    return (
      <Field label="服务商" id="mailbox-provider">
        <p id="mailbox-provider" className="text-sm">
          {recognised.name}
        </p>
      </Field>
    );
  }
  if (!form.address.includes("@")) return null;
  const hosted = providers.filter((p) => p.domains.length === 0);
  return (
    <Field label="服务商" id="mailbox-provider">
      <NativeSelect
        id="mailbox-provider"
        value={form.provider}
        onChange={(e) => {
          const id = e.target.value;
          const p = hosted.find((h) => h.id === id);
          onChange(p ?? CUSTOM);
        }}
      >
        <NativeSelectOption value="" disabled>
          选择服务商
        </NativeSelectOption>
        {hosted.map((p) => (
          <NativeSelectOption key={p.id} value={p.id}>
            {p.name}
          </NativeSelectOption>
        ))}
        <NativeSelectOption value={CUSTOM}>其他（自己填服务器）</NativeSelectOption>
      </NativeSelect>
      {form.provider === "" && (
        <p className="mt-1.5 text-sm text-muted-foreground">
          没认出这个邮箱的服务商。学校和公司的邮箱用的是腾讯、网易或阿里企业邮的，选对应的一项；都不是的选“其他”，在服务器设置里填写。
        </p>
      )}
    </Field>
  );
}

const SECURITY: [Security, string][] = [
  ["tls", "SSL/TLS"],
  ["starttls", "STARTTLS"],
];

function ServerFields({
  label,
  id,
  server,
  onChange,
}: {
  label: string;
  id: string;
  server: Server;
  onChange: (server: Server) => void;
}) {
  return (
    <fieldset className="grid min-w-0 grid-cols-[minmax(0,1fr)_88px_120px] gap-2 phone:grid-cols-[minmax(0,1fr)_80px]">
      <legend className="mb-1.5 text-sm text-muted-foreground">{label}</legend>
      <Input
        id={id}
        aria-label={`${label}服务器`}
        placeholder="服务器地址"
        spellCheck={false}
        value={server.host}
        onChange={(e) => onChange({ ...server, host: e.target.value })}
      />
      <Input
        aria-label={`${label}端口`}
        type="number"
        min={1}
        max={65535}
        aria-invalid={!whole(server.port, 1, U16)}
        value={Number.isNaN(server.port) ? "" : server.port}
        onChange={(e) => onChange({ ...server, port: e.target.valueAsNumber })}
      />
      <NativeSelect
        aria-label={`${label}加密方式`}
        className="phone:col-span-full"
        value={server.security}
        onChange={(e) => {
          const security = SECURITY.find(([value]) => value === e.target.value)?.[0];
          if (security) onChange({ ...server, security });
        }}
      >
        {SECURITY.map(([value, name]) => (
          <NativeSelectOption key={value} value={value}>
            {name}
          </NativeSelectOption>
        ))}
      </NativeSelect>
    </fieldset>
  );
}
