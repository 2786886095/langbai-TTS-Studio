import type { LongformJob } from "./LongformWorkspace";
import type { ServiceOptions } from "./serviceClient";

export type JobControl = "pause" | "stop" | "cancel" | "resume";
const activeStates = ["queued", "acquiring_gpu", "preparing", "loading", "warming", "generating", "pausing", "merging", "exporting", "verifying"];
const recoverableStates = ["paused", "interrupted", "failed", "storage_blocked"];
type Request = (path: string, options?: ServiceOptions) => Promise<LongformJob>;

/** Refresh on each bounded attempt: progress updates must not make Pause unusable. */
export async function controlLongformJob(id: string, action: JobControl, request: Request, extra: Record<string, unknown> = {}) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const current = await request(`/api/jobs/v2/${encodeURIComponent(id)}`);
    const status = current.status;
    if (action === "resume" && activeStates.includes(status)) return { job: current, message: "任务已经在运行。" };
    if (["merging", "exporting", "verifying"].includes(status)) return { job: current, message: "正文已完成，正在合并、导出或校验；当前阶段请等待成品发布。" };
    if (action === "pause" && status === "pausing") return { job: current, message: "已请求暂停，正在等待当前段完成。" };
    if (action === "pause" && !activeStates.includes(status)) return { job: current, message: "任务当前未在运行，无需再次暂停。" };
    if (action === "stop" && !activeStates.includes(status)) return { job: current, message: "任务当前未在运行。" };
    if (action === "cancel" && ["completed", "cancelled"].includes(status)) return { job: current, message: status === "completed" ? "任务已经完成。" : "任务已经取消。" };
    if (action === "resume" && !recoverableStates.includes(status)) throw new Error("当前状态不能继续；已取消任务请复制为新任务。");
    try {
      const updated = await request(`/api/jobs/v2/${encodeURIComponent(id)}/${action}`, { method: "POST", body: { version: current.version, ...extra } });
      return { job: updated, message: action === "pause" ? "已请求段后暂停，当前段完成后停止。" : action === "stop" ? "已请求立即停止，请等待进程退出确认。" : action === "cancel" ? "已请求取消任务。" : "任务已继续。" };
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      if (!/任务已被其他操作更新|版本.*冲突|version.*conflict/i.test(message) || attempt === 2) throw reason;
    }
  }
  throw new Error("任务状态持续变化，请稍后重试。");
}
