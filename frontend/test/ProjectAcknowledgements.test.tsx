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
        "感谢为项目提供模型额度与开发支持的同学们，完整名单与贡献记录见官网。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("支持名单：羽华丶")).toBeInTheDocument();
    expect(screen.getByText("羽华丶")).toBeInTheDocument();
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
