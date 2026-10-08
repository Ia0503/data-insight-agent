import { defineStore } from "pinia";
import { ref } from "vue";
import { api, message } from "../api";
import type { Project } from "../types";

export const useProjects = defineStore("projects", () => {
  const projects = ref<Project[]>([]);
  const loading = ref(false);
  const error = ref("");
  let generation = 0;
  async function load() {
    const current = ++generation;
    loading.value = true;
    error.value = "";
    try {
      const value = await api.projects();
      // 重新进入列表可能与旧刷新请求重叠，只应用最近一次请求的结果。
      if (current === generation) projects.value = value;
    } catch (e) {
      if (current === generation) error.value = message(e);
    } finally {
      if (current === generation) loading.value = false;
    }
  }
  return { projects, loading, error, load };
});
