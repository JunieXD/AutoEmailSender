import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { ProfessorTagAssignmentDialog } from "./ProfessorTagAssignmentDialog";
import type { ProfessorTagDTO } from "@/types";

const tags: ProfessorTagDTO[] = [
  {
    id: 1,
    name: "高意愿",
    text_color: "#166534",
    background_color: "#dcfce7",
  },
];

const createdTag: ProfessorTagDTO = {
  id: 2,
  name: "已联系",
  text_color: "#166534",
  background_color: "#dcfce7",
};

const deferred = <T,>() => {
  let resolve: (value: T) => void = () => {};
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve;
  });
  return { promise, resolve };
};

describe("ProfessorTagAssignmentDialog", () => {
  it("resets the custom tag form after closing and reopening", () => {
    const Harness = () => {
      const [open, setOpen] = useState(false);
      const [selectedTagIds, setSelectedTagIds] = useState<number[]>([]);

      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            打开
          </button>
          <ProfessorTagAssignmentDialog
            open={open}
            scopeKey="mentor-a"
            professorName="导师甲"
            tags={tags}
            selectedTagIds={selectedTagIds}
            onChange={setSelectedTagIds}
            onCreateTag={vi.fn()}
            onSave={vi.fn()}
            onClose={() => setOpen(false)}
          />
        </>
      );
    };

    render(<Harness />);

    fireEvent.click(screen.getByRole("button", { name: "打开" }));
    fireEvent.click(screen.getByRole("button", { name: "新增标签" }));
    fireEvent.change(screen.getByRole("textbox", { name: "新增标签名" }), {
      target: { value: "未保存标签" },
    });
    fireEvent.click(screen.getByRole("button", { name: "关闭标签选择" }));

    fireEvent.click(screen.getByRole("button", { name: "打开" }));

    expect(
      screen.queryByRole("textbox", { name: "新增标签名" }),
    ).not.toBeInTheDocument();
  });

  it("does not apply a completed create request to a different reopened professor", async () => {
    const createRequest = deferred<ProfessorTagDTO | null>();
    const handleCreateTag = vi.fn(() => createRequest.promise);
    const handleChange = vi.fn();

    const Harness = () => {
      const [open, setOpen] = useState(false);
      const [professorName, setProfessorName] = useState("导师甲");
      const [selectedTagIds, setSelectedTagIds] = useState<number[]>([]);
      const changeSelectedTagIds = (tagIds: number[]) => {
        handleChange(tagIds);
        setSelectedTagIds(tagIds);
      };

      return (
        <>
          <button
            type="button"
            onClick={() => {
              setProfessorName("导师甲");
              setOpen(true);
            }}
          >
            打开甲
          </button>
          <button
            type="button"
            onClick={() => {
              setProfessorName("导师乙");
              setSelectedTagIds([]);
              setOpen(true);
            }}
          >
            打开乙
          </button>
          <button type="button" onClick={() => setOpen(false)}>
            关闭
          </button>
          <ProfessorTagAssignmentDialog
            open={open}
            scopeKey={professorName}
            professorName={professorName}
            tags={tags}
            selectedTagIds={selectedTagIds}
            onChange={changeSelectedTagIds}
            onCreateTag={handleCreateTag}
            onSave={vi.fn()}
            onClose={() => setOpen(false)}
          />
        </>
      );
    };

    render(<Harness />);

    fireEvent.click(screen.getByRole("button", { name: "打开甲" }));
    fireEvent.click(screen.getByRole("button", { name: "新增标签" }));
    fireEvent.change(screen.getByRole("textbox", { name: "新增标签名" }), {
      target: { value: "已联系" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建标签" }));

    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    fireEvent.click(screen.getByRole("button", { name: "打开乙" }));

    createRequest.resolve(createdTag);

    await waitFor(() => {
      expect(handleCreateTag).toHaveBeenCalledTimes(1);
    });
    expect(handleChange).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "选择标签 高意愿" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("does not allow tag selection or saving while creating a custom tag", () => {
    const createRequest = deferred<ProfessorTagDTO | null>();
    const handleSave = vi.fn();
    const handleChange = vi.fn();
    const handleClose = vi.fn();

    const { rerender } = render(
      <ProfessorTagAssignmentDialog
        open
        scopeKey="mentor-a"
        professorName="导师甲"
        tags={tags}
        selectedTagIds={[]}
        creating={false}
        onChange={handleChange}
        onCreateTag={() => createRequest.promise}
        onSave={handleSave}
        onClose={handleClose}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "新增标签" }));
    fireEvent.change(screen.getByRole("textbox", { name: "新增标签名" }), {
      target: { value: "已联系" },
    });

    rerender(
      <ProfessorTagAssignmentDialog
        open
        scopeKey="mentor-a"
        professorName="导师甲"
        tags={tags}
        selectedTagIds={[]}
        creating
        onChange={handleChange}
        onCreateTag={() => createRequest.promise}
        onSave={handleSave}
        onClose={handleClose}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "选择标签 高意愿" }));
    fireEvent.click(screen.getByRole("button", { name: "保存标签" }));
    fireEvent.click(screen.getByRole("button", { name: "关闭标签选择" }));
    fireEvent.click(screen.getByRole("dialog", { name: "添加导师标签" }));

    expect(handleChange).not.toHaveBeenCalled();
    expect(handleSave).not.toHaveBeenCalled();
    expect(handleClose).not.toHaveBeenCalled();
  });

  it("does not auto-select a created tag after being closed before effects run", async () => {
    const createRequest = deferred<ProfessorTagDTO | null>();
    const handleChange = vi.fn();

    const { rerender } = render(
      <ProfessorTagAssignmentDialog
        open
        scopeKey="mentor-a"
        professorName="导师甲"
        tags={tags}
        selectedTagIds={[]}
        onChange={handleChange}
        onCreateTag={() => createRequest.promise}
        onSave={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "新增标签" }));
    fireEvent.change(screen.getByRole("textbox", { name: "新增标签名" }), {
      target: { value: "已联系" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建标签" }));

    rerender(
      <ProfessorTagAssignmentDialog
        open={false}
        scopeKey={null}
        professorName=""
        tags={tags}
        selectedTagIds={[]}
        onChange={handleChange}
        onCreateTag={() => createRequest.promise}
        onSave={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    createRequest.resolve(createdTag);

    await waitFor(() => {
      expect(handleChange).not.toHaveBeenCalled();
    });
  });

  it("prevents duplicate create requests from rapid repeated clicks", () => {
    const createRequest = deferred<ProfessorTagDTO | null>();
    const handleCreateTag = vi.fn(() => createRequest.promise);

    render(
      <ProfessorTagAssignmentDialog
        open
        scopeKey="mentor-a"
        professorName="导师甲"
        tags={tags}
        selectedTagIds={[]}
        creating={false}
        onChange={vi.fn()}
        onCreateTag={handleCreateTag}
        onSave={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "新增标签" }));
    fireEvent.change(screen.getByRole("textbox", { name: "新增标签名" }), {
      target: { value: "已联系" },
    });
    const createButton = screen.getByRole("button", { name: "创建标签" });

    fireEvent.click(createButton);
    fireEvent.click(createButton);

    expect(handleCreateTag).toHaveBeenCalledTimes(1);
  });
});
