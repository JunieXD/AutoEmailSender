import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Sparkles, X } from "lucide-react";
import qqGroupQrCode from "@/assets/qq-group-952383261.png";
import {
  MODAL_BACKDROP_CLASS_NAME,
  MODAL_SURFACE_CLASS_NAME,
} from "@/components/atoms/modalStyles";
import { QQ_GROUP_NUMBER } from "@/components/molecules/QqGroupButton";
import { getDesktopAppVersion, isDesktopApp } from "@/lib/desktopApi";
import { openExternalHttpUrl } from "@/lib/externalUrls";
import { useDismissableLayerClick } from "@/lib/useDismissableLayerClick";

const NOTICE_SEEN_KEY = "final_version_notice_seen";
const WEBSITE_URL = "https://juniexd.github.io/AutoEmailSender/";

export function FinalVersionNotice() {
  if (!isDesktopApp()) {
    return null;
  }

  return <FinalVersionNoticeInner />;
}

function FinalVersionNoticeInner() {
  const [version, setVersion] = useState("加载中");
  const [open, setOpen] = useState(
    () => window.localStorage.getItem(NOTICE_SEEN_KEY) === null,
  );

  useEffect(() => {
    void getDesktopAppVersion().then(setVersion);
  }, []);

  const close = () => {
    window.localStorage.setItem(NOTICE_SEEN_KEY, "1");
    setOpen(false);
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex min-h-[2.8rem] min-w-[11rem] items-center gap-2 rounded-2xl border border-stone-200/90 bg-white/92 px-3 py-2 text-left text-stone-700 shadow-sm shadow-stone-200/45 backdrop-blur-sm transition hover:border-stone-300 hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/15"
      >
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary/8 text-primary">
          <Sparkles className="h-4 w-4" />
        </span>
        <span className="flex min-w-0 flex-1 flex-col items-start leading-none">
          <span className="text-[11px] font-medium tracking-[0.03em] text-stone-500">
            v{version} 最终版
          </span>
          <span className="mt-1 text-[13px] font-medium text-stone-900">V3 开发中</span>
        </span>
      </button>
      {open ? <FinalVersionDialog version={version} onClose={close} /> : null}
    </>
  );
}

function FinalVersionDialog({ version, onClose }: { version: string; onClose: () => void }) {
  const { onBackdropClick, onBackdropMouseDown, onContentClick, onContentMouseDown } =
    useDismissableLayerClick(onClose);

  return createPortal(
    <div
      className={`${MODAL_BACKDROP_CLASS_NAME} z-[90]`}
      role="presentation"
      onClick={onBackdropClick}
      onMouseDown={onBackdropMouseDown}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="final-version-notice-title"
        className={`${MODAL_SURFACE_CLASS_NAME} w-full max-w-xl`}
        onClick={onContentClick}
        onMouseDown={onContentMouseDown}
      >
        <div className="flex items-start justify-between gap-4 border-b border-stone-100 px-6 py-5">
          <div>
            <h3 id="final-version-notice-title" className="text-lg font-semibold text-stone-900">
              V2 已停止更新
            </h3>
            <p className="mt-1 text-sm text-stone-500">当前 v{version}，是 V2 的最终版本</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-stone-300 hover:text-stone-900"
            aria-label="关闭停更说明"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="space-y-4 px-6 py-5 text-sm leading-7 text-stone-700">
          <p>
            感谢你一路使用 Auto Email Sender V2。V2 之后不再更新，但会一直可以正常使用，本机的导师、草稿和邮件记录都不受影响。
          </p>
          <p>
            我们正在从头打造全新的 V3：把大家反馈过的问题和期待的功能重新设计、认真实现，带来更快、更稳、更顺手的体验。V3 是独立的新应用，发布后需要单独下载安装，不会覆盖 V2。
          </p>
          <div className="flex items-center gap-4 rounded-2xl border border-primary/10 bg-primary/5 p-3">
            <img
              src={qqGroupQrCode}
              alt={`QQ群 ${QQ_GROUP_NUMBER} 二维码`}
              className="h-28 w-28 shrink-0 rounded-xl border border-stone-200/90 bg-white object-contain p-1"
            />
            <div>
              <p className="font-medium text-stone-900">想第一时间知道 V3 的进展和发布消息？</p>
              <p className="mt-1">
                欢迎加入 QQ 交流群：
                <span className="font-semibold tracking-[0.06em] text-stone-900">
                  {QQ_GROUP_NUMBER}
                </span>
              </p>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-3 border-t border-stone-100 px-6 py-4">
          <button
            type="button"
            onClick={() => openExternalHttpUrl(WEBSITE_URL)}
            className="rounded-2xl border border-stone-200 bg-white px-4 py-2.5 text-sm font-medium text-stone-700 transition hover:border-stone-300 hover:bg-stone-50"
          >
            打开官网
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-2xl bg-primary px-4 py-2.5 text-sm font-semibold text-white shadow-sm shadow-primary/20 transition hover:bg-primary/90"
          >
            知道了
          </button>
        </div>
      </section>
    </div>,
    document.body,
  );
}
