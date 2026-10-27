import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { call, errorMessage } from "@/api/call";
import type { AgentCli, McpClient } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import { Row, Section, SwitchButton } from "./settings";

const CLIENTS: [McpClient, string, string][] = [
  ["claudeDesktop", "Claude Desktop", "重新打开 Claude Desktop 后生效。"],
  ["claudeCode", "Claude Code", "新开的 Claude Code 会话里生效。"],
  ["cursor", "Cursor", "重新打开 Cursor 后生效。"],
];

/** Settings › 应用 › Agent (docs/ui.md 设置 › 应用 › 手机和 Agent; the phone part comes later). */
export function AgentSection() {
  const queryClient = useQueryClient();
  const access = useQuery({ queryKey: ["getAgentAccess"], queryFn: () => call("getAgentAccess", {}) });
  const save = useMutation({
    mutationFn: (enabled: boolean) => call("setAgentAccess", { enabled }),
    onSuccess: async (_, enabled) => {
      await queryClient.cancelQueries({ queryKey: ["getAgentAccess"] });
      queryClient.setQueryData(["getAgentAccess"], enabled);
    },
  });
  return (
    <Section title="Agent">
      <div>
        <label className="flex items-center justify-between gap-4">
          <span>允许 Agent 连接</span>
          <SwitchButton
            label="允许 Agent 连接"
            checked={access.data ?? false}
            disabled={access.data === undefined || save.isPending}
            onClick={() => save.mutate(!access.data)}
          />
        </label>
        <div className="mt-2 space-y-2 text-sm text-muted-foreground">
          <p>
            开启后，你自己的 Agent（Claude、Cursor 这类聊天和编程助手，或者你写的脚本）可以通过 MCP
            或命令行使用这个应用。界面上能做的它都能做，包括替你填邮箱授权码和 API Key，但看不到已经保存的。
          </p>
          <p>发信给导师、永久删除、同意把数据发给模型服务商之前，应用会弹出“Agent 请求”，你点“允许”才会做。</p>
          <p>Agent 读到的导师资料、邮件和材料文字，会交给它所用的模型服务商。应用开着时 Agent 才能连上。</p>
        </div>
        {access.error && <p className="mt-2 text-sm text-destructive">设置未能加载：{errorMessage(access.error)}</p>}
        {save.error && <p className="mt-2 text-sm text-destructive">设置未能保存：{errorMessage(save.error)}</p>}
      </div>
      {access.data && (
        <>
          <McpRow />
          <CliRow />
        </>
      )}
    </Section>
  );
}

function McpRow() {
  const [open, setOpen] = useState<McpClient | null>(null);
  const [written, setWritten] = useState<McpClient[]>([]);
  const text = useQuery({ queryKey: ["mcpConfigText"], queryFn: () => call("mcpConfigText", {}) });
  const copy = useMutation({ mutationFn: (config: string) => navigator.clipboard.writeText(config) });
  return (
    <Row label="MCP">
      <p className="text-sm text-muted-foreground">
        接入后，在这些工具里直接说要做什么，比如“把这个网页上的导师加进 Auto Email Sender”。
      </p>
      <ul className="divide-y divide-border rounded-lg border border-border">
        {CLIENTS.map(([client, name, effect]) => (
          <li key={client} className="flex items-center justify-between gap-4 px-4 py-2.5">
            <div className="min-w-0">
              <p>{name}</p>
              {written.includes(client) && <p className="text-sm text-muted-foreground">已写入，{effect}</p>}
            </div>
            <Button variant="quiet" className="shrink-0" onClick={() => setOpen(client)}>
              写入配置
            </Button>
          </li>
        ))}
      </ul>
      <p className="mt-1 text-sm text-muted-foreground">别的工具：把下面这段加进它的 MCP 配置。</p>
      {text.data && (
        <pre className="rounded-lg bg-muted px-4 py-3 text-xs leading-relaxed whitespace-pre-wrap wrap-anywhere">
          {text.data}
        </pre>
      )}
      {text.error && <p className="text-sm text-destructive">未能生成配置：{errorMessage(text.error)}</p>}
      <div className="flex items-center gap-3">
        <Button variant="quiet" disabled={!text.data} onClick={() => text.data && copy.mutate(text.data)}>
          复制
        </Button>
        {copy.isSuccess && <span className="text-sm text-muted-foreground">已复制</span>}
        {copy.error && <span className="text-sm text-destructive">未能复制：{errorMessage(copy.error)}</span>}
      </div>
      {open && (
        <McpDialog
          client={open}
          onClose={() => setOpen(null)}
          onWritten={() => {
            setWritten((clients) => [...clients, open]);
            setOpen(null);
          }}
        />
      )}
    </Row>
  );
}

/** What writing the configuration changes, shown before it is written (docs/ui.md). */
function McpDialog({ client, onClose, onWritten }: { client: McpClient; onClose: () => void; onWritten: () => void }) {
  const name = CLIENTS.find(([c]) => c === client)?.[1];
  const preview = useQuery({
    queryKey: ["previewMcpConfig", client],
    queryFn: () => call("previewMcpConfig", { client }),
    gcTime: 0,
  });
  const write = useMutation({ mutationFn: () => call("writeMcpConfig", { client }), onSuccess: onWritten });
  const p = preview.data;
  return (
    <Dialog open onOpenChange={(open) => !open && !write.isPending && onClose()}>
      <DialogContent className="w-[560px]">
        <DialogHeader>写入 {name} 的配置</DialogHeader>
        <DialogBody className="space-y-3 py-4 text-sm">
          {p && (
            <>
              {p.commands.length > 0 ? (
                <>
                  <p>{p.writeUnavailable ?? "会用 Claude Code 自己的命令添加，它把配置存在："}</p>
                  <p className="break-all text-muted-foreground">{p.path}</p>
                  <p>运行的命令：</p>
                  <pre className="rounded-lg bg-muted px-4 py-3 text-xs leading-relaxed whitespace-pre-wrap wrap-anywhere">
                    {p.commands.join("\n")}
                  </pre>
                </>
              ) : (
                <>
                  <p>会改这个文件，其他内容不动：</p>
                  <p className="break-all text-muted-foreground">{p.path}</p>
                </>
              )}
              <p>{p.exists ? "里面已经有 autoemail，会换成：" : "加入的内容："}</p>
              <pre className="rounded-lg bg-muted px-4 py-3 text-xs leading-relaxed whitespace-pre-wrap wrap-anywhere">
                {p.server}
              </pre>
            </>
          )}
          {preview.isPending && <p className="text-muted-foreground">正在读取配置…</p>}
          {preview.error && <p className="text-destructive">无法写入：{errorMessage(preview.error)}</p>}
          {write.error && <p className="text-destructive">未能写入：{errorMessage(write.error)}</p>}
        </DialogBody>
        <DialogFooter className="justify-end">
          <Button variant="ghost" onClick={onClose} disabled={write.isPending}>
            取消
          </Button>
          <Button onClick={() => write.mutate()} disabled={!p || p.writeUnavailable !== null || write.isPending}>
            写入
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CliRow() {
  const queryClient = useQueryClient();
  const cli = useQuery({ queryKey: ["getAgentCli"], queryFn: () => call("getAgentCli", {}) });
  const onSuccess = (data: AgentCli) => queryClient.setQueryData(["getAgentCli"], data);
  const install = useMutation({ mutationFn: () => call("installAgentCli", {}), onSuccess });
  const uninstall = useMutation({ mutationFn: () => call("uninstallAgentCli", {}), onSuccess });
  const busy = install.isPending || uninstall.isPending;
  const failed = install.error ?? uninstall.error;
  return (
    <Row label="命令行">
      {cli.data?.state.kind === "installed" ? (
        <>
          <p className="text-sm text-muted-foreground">
            已安装，在终端里输入 <code>{cli.data.command}</code> 使用，比如 <code>{cli.data.command} list</code>{" "}
            列出能做的事。装在 <span className="break-all">{cli.data.location}</span>。
          </p>
          <Button variant="quiet" className="self-start" disabled={busy} onClick={() => uninstall.mutate()}>
            卸载
          </Button>
        </>
      ) : (
        <>
          <p className="text-sm text-muted-foreground">
            给只能运行命令的 Agent 和脚本用。装好后在终端里直接输入 autoemail 就能用。macOS 上可能要输入电脑的密码。
          </p>
          <Button variant="quiet" className="self-start" disabled={!cli.data || busy} onClick={() => install.mutate()}>
            安装命令行工具
          </Button>
        </>
      )}
      {cli.error && <p className="text-sm text-destructive">未能读取：{errorMessage(cli.error)}</p>}
      {failed && <p className="text-sm text-destructive">{errorMessage(failed)}</p>}
    </Row>
  );
}
