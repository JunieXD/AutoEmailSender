import { Marked } from "marked";
import cjkFriendly from "marked-cjk-friendly";
import { useState } from "react";
import plus from "../../legal/plus.md?raw";
import privacy from "../../legal/privacy.md?raw";
import terms from "../../legal/terms.md?raw";
import { openLinks } from "@/components/correspondence";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogClose, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";

// The terms the core sends the server the version of (crates/account), shown in the app as written.

const LEGAL = {
  terms: { title: "用户服务协议", text: terms },
  privacy: { title: "隐私政策", text: privacy },
  plus: { title: "Plus 服务协议", text: plus },
} as const;

export type Legal = keyof typeof LEGAL;

/**
 * What changed in the terms of service and privacy policy since the previous version, shown to
 * accounts that agreed to that one (docs/development.md#条款). Rewrite it with each new version.
 */
export const TERMS_CHANGES =
  "新增《用户服务协议》和《隐私政策》，写明使用账号和官方服务的规则，以及处理哪些个人信息、如何保护。";

// The texts are the app's own, with headings and tables a letter never has, so not the letters' parser.
const parser = new Marked(cjkFriendly());

const LEGAL_BODY =
  "text-sm leading-relaxed [&_a]:text-brand-text [&_a]:underline [&_h1]:text-lg [&_h1]:font-semibold [&_h2]:mt-5 [&_h2]:font-semibold [&_li]:mt-1 [&_ol]:list-decimal [&_ol]:pl-6 [&_p]:mt-2 [&_strong]:font-semibold [&_table]:mt-2 [&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:border-border [&_th]:px-2 [&_th]:py-1 [&_th]:text-left [&_ul]:list-disc [&_ul]:pl-6";

function LegalDialog({ legal, onClose }: { legal: Legal; onClose: () => void }) {
  const { title, text } = LEGAL[legal];
  return (
    <Dialog open onOpenChange={onClose}>
      <DialogContent>
        <DialogHeader>{title}</DialogHeader>
        <DialogBody>
          <div
            className={LEGAL_BODY}
            dangerouslySetInnerHTML={{ __html: parser.parse(text, { async: false }) }}
            onClick={openLinks}
          />
        </DialogBody>
        <DialogFooter className="justify-end">
          <DialogClose render={<Button variant="quiet" />}>关闭</DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** 《用户服务协议》 and the like, opening the text in the app. */
export function LegalLink({ legal }: { legal: Legal }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="text-brand-text hover:underline" onClick={() => setOpen(true)}>
        《{LEGAL[legal].title}》
      </button>
      {open && <LegalDialog legal={legal} onClose={() => setOpen(false)} />}
    </>
  );
}
