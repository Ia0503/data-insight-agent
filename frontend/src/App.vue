<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { request } from "./api";
const connected = ref<boolean | null>(null);
const showConnection = ref(false);
let initialStatusTimer: ReturnType<typeof setTimeout> | undefined;
let healthPoll: ReturnType<typeof setInterval> | undefined;
let checking = false;
let mounted = true;
async function checkHealth() {
  if (checking) return;
  checking = true;
  try {
    await request("/health");
    if (mounted) connected.value = true;
  } catch {
    if (mounted) connected.value = false;
  } finally {
    checking = false;
    if (mounted) {
      if (initialStatusTimer) clearTimeout(initialStatusTimer);
      showConnection.value = true;
    }
  }
}
onMounted(() => {
  // 快速响应直接显示结果；慢连接才提示等待，避免刷新时闪过中间状态。
  initialStatusTimer = setTimeout(() => {
    showConnection.value = true;
  }, 400);
  void checkHealth();
  healthPoll = setInterval(checkHealth, 30_000);
});
onUnmounted(() => {
  mounted = false;
  if (healthPoll) clearInterval(healthPoll);
  if (initialStatusTimer) clearTimeout(initialStatusTimer);
});
</script>

<template>
  <div class="app-layout">
    <header class="site-header">
      <div class="header-inner">
        <RouterLink to="/" class="site-brand">业务分析工作台</RouterLink>
        <nav aria-label="主导航">
          <RouterLink to="/" class="nav-link nav-active">项目</RouterLink>
        </nav>
        <span
          class="connection"
          :class="{
            'connection-hidden': !showConnection,
            unavailable: connected === false,
            connecting: connected === null,
          }"
          role="status"
          :aria-busy="connected === null"
        >
          <span class="connection-label">
            {{
              !showConnection
                ? ""
                : connected === null
                  ? "正在连接"
                  : connected
                    ? "服务已连接"
                    : "服务不可用，请检查后端"
            }}
          </span>
          <span class="connection-short" aria-hidden="true">{{
            !showConnection
              ? ""
              : connected === null
                ? "连接中"
                : connected
                  ? "已连接"
                  : ""
          }}</span>
        </span>
      </div>
    </header>
    <main><RouterView /></main>
  </div>
</template>
