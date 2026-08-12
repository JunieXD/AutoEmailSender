import { Activity, useCallback, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useSelectionContext } from "@/context/SelectionContext";
import {
  isAgentCrawlJobHandoff,
  isAgentTaskCenterHandoff,
} from "@/features/agent-ui-handoffs/types";
import { useAgentUiHandoffSurface } from "@/features/agent-ui-handoffs/useAgentUiHandoffSurface";
import { EmailDeliveryPlan } from "@/features/email-deliveries/components/EmailDeliveryPlan";
import type { TaskCenterSection } from "@/features/email-deliveries/components/TaskCenterSectionSwitch";
import { getCrawlJobDetails } from "@/lib/api/crawlJobsApi";
import { getEmailTaskThread } from "@/lib/api/emailTasksApi";
import {
  BackgroundTasksPage,
  type PendingCrawlJobHandoff,
} from "@/pages/BackgroundTasksPage";

export {
  CrawlJobCard,
  TaskListViewSwitch,
} from "@/pages/BackgroundTasksPage";

export const TasksPage = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const { selectedIdentityId, setSelectedIdentityId } = useSelectionContext();
  const [pendingCrawlJobHandoff, setPendingCrawlJobHandoff] =
    useState<PendingCrawlJobHandoff | null>(null);
  const crawlHandoffTokenRef = useRef(0);
  const section: TaskCenterSection =
    searchParams.get("section") === "background" ? "background" : "delivery";

  const updateSection = useCallback(
    (nextSection: TaskCenterSection) => {
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        next.set("section", nextSection);
        if (nextSection === "delivery") {
          next.delete("batch_task_id");
        } else {
          next.delete("task_id");
          next.delete("view");
          next.delete("identity_id");
          next.delete("source");
          next.delete("status");
          next.delete("q");
        }
        return next;
      });
    },
    [setSearchParams],
  );

  const openBatchTask = useCallback(
    (identityId: number, batchTaskId: number) => {
      setSelectedIdentityId(identityId);
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        next.set("section", "background");
        next.set("batch_task_id", String(batchTaskId));
        next.delete("task_id");
        return next;
      });
    },
    [setSearchParams, setSelectedIdentityId],
  );

  useAgentUiHandoffSurface("tasks.center", async (handoff) => {
    if (!isAgentTaskCenterHandoff(handoff)) {
      return {
        status: "failed",
        failureMessage: "任务中心收到的界面交接类型不匹配。",
      };
    }
    if (selectedIdentityId !== handoff.payload.identity_id) {
      return {
        status: "failed",
        failureMessage: "任务中心尚未切换到界面交接指定的发件身份。",
      };
    }
    const data = await getEmailTaskThread(handoff.payload.task_id);
    if (
      data.current_task?.id !== handoff.payload.task_id ||
      data.professor.id !== handoff.payload.professor_id ||
      data.identity.id !== handoff.payload.identity_id
    ) {
      throw new Error("邮件任务详情与界面交接不匹配。");
    }
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("section", "delivery");
      next.set("task_id", String(handoff.payload.task_id));
      next.delete("batch_task_id");
      next.delete("identity_id");
      next.delete("source");
      next.delete("status");
      next.delete("q");
      return next;
    }, { replace: true });
    return {
      status: "applied",
      result: {
        surface: handoff.surface,
        task_id: handoff.payload.task_id,
        professor_id: handoff.payload.professor_id,
        identity_id: handoff.payload.identity_id,
        section: "delivery",
        resource_verified: true,
      },
    };
  });

  useAgentUiHandoffSurface("crawler.job", async (handoff) => {
    if (!isAgentCrawlJobHandoff(handoff)) {
      return {
        status: "failed",
        failureMessage: "任务中心收到的抓取任务界面交接类型不匹配。",
      };
    }
    const jobId = handoff.payload.job_id;
    const data = await getCrawlJobDetails(jobId);
    if (data.job.id !== jobId) {
      throw new Error("抓取任务详情与界面交接不匹配。");
    }
    const token = crawlHandoffTokenRef.current + 1;
    crawlHandoffTokenRef.current = token;
    setPendingCrawlJobHandoff({ token, data });
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("section", "background");
      next.delete("task_id");
      next.delete("batch_task_id");
      next.delete("view");
      next.delete("identity_id");
      next.delete("source");
      next.delete("status");
      next.delete("q");
      return next;
    }, { replace: true });
    return {
      status: "applied",
      result: {
        surface: handoff.surface,
        job_id: jobId,
        task_view: data.job.deleted_at ? "trash" : "current",
        details_open: true,
      },
    };
  });

  const handleCrawlHandoffApplied = useCallback((token: number) => {
    setPendingCrawlJobHandoff((current) =>
      current?.token === token ? null : current,
    );
  }, []);

  return (
    <>
      <Activity mode={section === "delivery" ? "visible" : "hidden"}>
        <EmailDeliveryPlan
          onSectionChange={updateSection}
          onOpenBatchTask={openBatchTask}
        />
      </Activity>
      <Activity mode={section === "background" ? "visible" : "hidden"}>
        <BackgroundTasksPage
          pendingCrawlJobHandoff={pendingCrawlJobHandoff}
          onCrawlHandoffApplied={handleCrawlHandoffApplied}
        />
      </Activity>
    </>
  );
};
