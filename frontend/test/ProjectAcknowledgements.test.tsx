import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProjectAcknowledgements } from "@/components/molecules/ProjectAcknowledgements";

describe("ProjectAcknowledgements", () => {
  afterEach(() => {
    Reflect.deleteProperty(window, "autoEmailSender");
    vi.restoreAllMocks();
  });

  it("shows a scalable supporter list without contribution amounts", () => {
    render(<ProjectAcknowledgements />);

    expect(screen.getByText("致谢")).toBeInTheDocument();
    expect(screen.queryByText("关于与致谢")).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "感谢所有提出建议、报告问题、参与测试和完善数据的群友。",
      ),
    ).toBeInTheDocument();
    const supporterList = screen.getByLabelText(/^社区致谢名单：/);
    expect(supporterList).toBeInTheDocument();
    expect(supporterList.getAttribute("aria-label")).toContain("woodfishhhh");
    expect(supporterList.getAttribute("aria-label")).toContain("thosehow");
    expect(supporterList.getAttribute("aria-label")).toContain("青青草原领头羊");
    expect(supporterList.getAttribute("aria-label")).not.toContain("JunieXD");
    expect(
      screen.getByText("疯狂轮指八度音、羽华丶、奇华、000、thosehow、Lestine-Yan"),
    ).toBeInTheDocument();
    expect(screen.getByText("展开其余 54 位群友")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("(QQ:");
    expect(screen.queryByText(/US\$800|800 美元/)).not.toBeInTheDocument();
  });

  it("opens the complete acknowledgement in the browser", () => {
    const openWindow = vi.spyOn(window, "open").mockImplementation(() => null);
    render(<ProjectAcknowledgements />);

    fireEvent.click(
      screen.getByRole("button", { name: "查看完整致谢，在浏览器中打开" }),
    );

    expect(openWindow).toHaveBeenCalledWith(
      "https://juniexd.github.io/AutoEmailSender/acknowledgements",
      "_blank",
      "noopener,noreferrer",
    );
  });
});
