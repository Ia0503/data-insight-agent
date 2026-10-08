import { onUnmounted, ref } from "vue";
import { activeRun, agentApi, type AgentEvent, type AgentRun } from "../agent";

export function useAgentStream(
  update: (state: Partial<AgentRun>) => void,
  append: (events: AgentEvent[]) => void,
  completed: () => Promise<void>,
) {
  const connection = ref("未连接");
  let source: EventSource | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let version = 0;
  let last = 0;
  function stop() {
    version++;
    source?.close();
    source = undefined;
    if (timer) clearTimeout(timer);
    timer = undefined;
  }
  function start(project: string, run: string, after: number) {
    stop();
    const current = version;
    last = after;
    let polling = false;
    const valid = () => current === version;
    function watchdog(delay = 35000) {
      if (timer) clearTimeout(timer);
      timer = setTimeout(fallback, delay);
    }
    function accept(events: AgentEvent[]) {
      const fresh = events.filter(
        (e) => Number.isInteger(e.sequence) && e.sequence > last,
      );
      fresh.sort((a, b) => a.sequence - b.sequence);
      if (fresh.length) {
        last = fresh.at(-1)!.sequence;
        append(fresh);
      }
    }
    async function poll() {
      try {
        const [state, events] = await Promise.all([
          agentApi.run(project, run),
          agentApi.events(project, run, last),
        ]);
        if (!valid()) return;
        accept(events);
        update(state);
        connection.value = "定时查询";
        if (!activeRun(state.status)) {
          stop();
          connection.value = "已结束";
          await completed();
          return;
        }
      } catch {
        if (valid()) connection.value = "连接中断，稍后重试";
      }
      if (valid()) timer = setTimeout(poll, 1500);
    }
    function fallback() {
      if (!valid() || polling) return;
      polling = true;
      source?.close();
      source = undefined;
      connection.value = "实时连接不可用，改用定时查询";
      if (timer) clearTimeout(timer);
      timer = setTimeout(poll, 0);
    }
    connection.value = "正在连接";
    source = new EventSource(
      `/api/projects/${project}/agent/runs/${run}/stream?after=${last}`,
    );
    source.onopen = () => {
      if (valid()) {
        connection.value = "实时连接";
      }
    };
    source.addEventListener("progress", (event) => {
      if (!valid()) return;
      try {
        const value = JSON.parse((event as MessageEvent).data) as AgentEvent;
        if (typeof value.kind !== "string" || typeof value.message !== "string")
          throw new Error();
        accept([value]);
        watchdog();
      } catch {
        fallback();
      }
    });
    source.addEventListener("state", (event) => {
      if (!valid()) return;
      try {
        const state = JSON.parse(
          (event as MessageEvent).data,
        ) as Partial<AgentRun>;
        if (
          !state.status ||
          ![
            "queued",
            "running",
            "succeeded",
            "failed",
            "cancelled",
            "interrupted",
          ].includes(state.status)
        )
          throw new Error();
        update(state);
        watchdog();
      } catch {
        fallback();
      }
    });
    source.addEventListener("complete", () => {
      if (!valid()) return;
      stop();
      connection.value = "已结束";
      void completed();
    });
    source.addEventListener("unavailable", fallback);
    source.addEventListener("heartbeat", () => {
      if (valid()) watchdog();
    });
    source.onerror = () => {
      if (!valid()) return;
      connection.value = "连接中断，正在重连";
      watchdog(4500);
    };
    // A silent/buffered proxy must not leave a task permanently without updates.
    watchdog(8000);
  }
  onUnmounted(stop);
  return { connection, start, stop };
}
