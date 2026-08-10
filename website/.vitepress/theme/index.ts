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

const LocalizedLayout = {
  setup() {
    let observer: MutationObserver | undefined;
    let frame = 0;

    onMounted(() => {
      const scheduleLocalization = () => {
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(localizeBuiltInLabels);
      };
      scheduleLocalization();
      observer = new MutationObserver(scheduleLocalization);
      observer.observe(document.body, { childList: true, subtree: true });
    });

    onBeforeUnmount(() => {
      observer?.disconnect();
      cancelAnimationFrame(frame);
    });

    return () => h(DefaultTheme.Layout!);
  },
};

export default {
  ...DefaultTheme,
  Layout: LocalizedLayout,
};
