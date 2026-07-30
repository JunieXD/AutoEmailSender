import { useEffect, useId, useRef, useState } from "react";
import clsx from "clsx";
import qqGroupQrCode from "@/assets/qq-group-952383261.png";
import { QqOutlineLogo } from "@/components/atoms/QqOutlineLogo";
import { FloatingMenuPortal } from "@/components/molecules/FloatingMenuPortal";

const QQ_GROUP_NUMBER = "952383261";

export const QqGroupButton = () => {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const popoverId = useId();
  const titleId = useId();

  useEffect(() => {
    if (!open) {
      return;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") {
        return;
      }

      event.preventDefault();
      setOpen(false);
      buttonRef.current?.focus();
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open]);

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        aria-label="加入 QQ 群"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={popoverId}
        title="加入 QQ 群"
        onClick={() => setOpen((previous) => !previous)}
        className={clsx(
          "inline-flex h-[2.8rem] w-[2.8rem] shrink-0 items-center justify-center rounded-2xl border border-stone-200/90 bg-white/92 shadow-sm shadow-stone-200/45 backdrop-blur-sm transition hover:border-primary/30 hover:bg-white hover:shadow-md hover:shadow-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20",
          open &&
            "border-primary/45 bg-white shadow-md shadow-primary/15 ring-2 ring-primary/10",
        )}
      >
        <span className="flex h-7 w-7 items-center justify-center rounded-[10px] border border-primary/10 bg-primary/8 text-primary shadow-sm shadow-primary/10">
          <QqOutlineLogo className="h-[19px] w-[19px]" />
        </span>
      </button>

      <FloatingMenuPortal
        open={open}
        anchorRef={buttonRef}
        align="left"
        minWidth={256}
        maxHeight={440}
        testId="qq-group-popover"
        className="!border-stone-200/90 !bg-white/98 !p-0"
        onClose={() => setOpen(false)}
      >
        <section
          id={popoverId}
          role="dialog"
          aria-labelledby={titleId}
          className="w-64"
        >
          <div className="flex items-center gap-3 border-b border-primary/10 bg-primary/5 px-3.5 py-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-primary/10 bg-primary/8 text-primary shadow-sm shadow-primary/10">
              <QqOutlineLogo className="h-[25px] w-[25px]" />
            </span>
            <div>
              <h2 id={titleId} className="text-sm font-semibold text-stone-900">
                QQ 交流群
              </h2>
              <p className="mt-0.5 text-[11px] text-stone-500">扫码加入交流与反馈</p>
            </div>
          </div>

          <div className="px-3.5 pb-3 pt-3">
            <p className="text-[13px] leading-5 text-stone-600">
              遇到问题欢迎在群内反馈。
            </p>
            <div className="mt-2.5 overflow-hidden rounded-2xl border border-stone-200/90 bg-white p-1.5 shadow-inner shadow-stone-100">
              <img
                src={qqGroupQrCode}
                alt={`QQ群 ${QQ_GROUP_NUMBER} 二维码`}
                className="aspect-square w-full object-contain"
              />
            </div>
            <p className="mt-2.5 rounded-xl bg-stone-50 px-3 py-2 text-center text-[13px] text-stone-600">
              QQ群号：
              <span className="font-semibold tracking-[0.06em] text-stone-900">
                {QQ_GROUP_NUMBER}
              </span>
            </p>
          </div>
        </section>
      </FloatingMenuPortal>
    </>
  );
};
