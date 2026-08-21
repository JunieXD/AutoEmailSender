import DefaultTheme from "vitepress/theme";
import { h, onBeforeUnmount, onMounted } from "vue";
import "./style.css";

const labelTranslations: Record<string, string> = {
  "Main Navigation": "主导航",
  "Sidebar Navigation": "侧边栏导航",
  Pager: "分页",
};

function localizeBuiltInLabels(): void {
  for (const [english, chinese] of Object.entries(labelTranslations)) {
    document.querySelectorAll<HTMLElement>(".visually-hidden").forEach((element) => {
      if (element.textContent?.trim() === english) element.textContent = chinese;
    });
  }

  document.querySelectorAll<HTMLElement>('[aria-label="extra navigation"]').forEach((element) => {
    element.setAttribute("aria-label", "更多导航");
  });
  document.querySelectorAll<HTMLElement>('[aria-label="mobile navigation"]').forEach((element) => {
    element.setAttribute("aria-label", "移动端导航");
  });
  document.querySelectorAll<HTMLAnchorElement>('a[aria-label^="Permalink to "]').forEach((anchor) => {
    const heading = anchor.closest("h1, h2, h3, h4, h5, h6")?.textContent?.replace("#", "").trim();
    if (heading) anchor.setAttribute("aria-label", `指向“${heading}”的固定链接`);
  });
}

function syncAcknowledgementOutline(): void {
  if (!document.querySelector<HTMLElement>(".acknowledgement-group")) return;

  const outline = document.querySelector<HTMLElement>(".VPDocAsideOutline");
  const activeLink = outline?.querySelector<HTMLElement>("a.active");
  if (!outline || !activeLink) return;

  const outlineRect = outline.getBoundingClientRect();
  const activeRect = activeLink.getBoundingClientRect();
  const edgePadding = 28;
  if (
    activeRect.top < outlineRect.top + edgePadding ||
    activeRect.bottom > outlineRect.bottom - edgePadding
  ) {
    activeLink.scrollIntoView({ block: "nearest", behavior: "auto" });
  }
}

const LocalizedLayout = {
  setup() {
    let observer: MutationObserver | undefined;
    let frame = 0;
    let outlineFrame = 0;

    const scheduleOutlineSync = () => {
      cancelAnimationFrame(outlineFrame);
      outlineFrame = requestAnimationFrame(syncAcknowledgementOutline);
    };

    onMounted(() => {
      const scheduleLocalization = () => {
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(localizeBuiltInLabels);
      };
      scheduleLocalization();
      observer = new MutationObserver(scheduleLocalization);
      observer.observe(document.body, { childList: true, subtree: true });
      window.addEventListener("scroll", scheduleOutlineSync, { passive: true });
      scheduleOutlineSync();
    });

    onBeforeUnmount(() => {
      observer?.disconnect();
      cancelAnimationFrame(frame);
      cancelAnimationFrame(outlineFrame);
      window.removeEventListener("scroll", scheduleOutlineSync);
    });

    return () => h(DefaultTheme.Layout!);
  },
};

export default {
  ...DefaultTheme,
  Layout: LocalizedLayout,
};
