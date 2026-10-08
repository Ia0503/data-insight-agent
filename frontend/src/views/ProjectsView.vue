<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api, dateLabel, message } from "../api";
import { useProjects } from "../stores/projects";
import ExampleImport from "../components/ExampleImport.vue";
const store = useProjects();
const router = useRouter();
const showForm = ref(false);
const name = ref("");
const description = ref("");
const saving = ref(false);
const error = ref("");
onMounted(store.load);
async function create() {
  if (saving.value) return;
  saving.value = true;
  error.value = "";
  try {
    const project = await api.create(name.value, description.value);
    await router.push(`/projects/${project.id}`);
  } catch (e) {
    error.value = message(e);
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <div class="page-heading">
    <div>
      <h1>项目</h1>
      <p class="muted">{{ store.projects.length }} 个项目</p>
    </div>
    <button class="primary" :disabled="saving" @click="showForm = !showForm">
      创建项目
    </button>
  </div>
  <section v-if="showForm" class="form-panel" aria-labelledby="create-heading">
    <h2 id="create-heading">创建项目</h2>
    <form @submit.prevent="create">
      <label
        >项目名称<input
          v-model="name"
          :disabled="saving"
          required
          maxlength="120"
          placeholder="例如：SaaS Q3 经营分析"
      /></label>
      <label
        >项目描述<textarea
          v-model="description"
          :disabled="saving"
          maxlength="2000"
          placeholder="分析背景与关注的问题"
        ></textarea>
      </label>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <div class="actions">
        <button type="button" :disabled="saving" @click="showForm = false">
          取消</button
        ><button class="primary" :disabled="saving">
          {{ saving ? "创建中…" : "保存项目" }}
        </button>
      </div>
    </form>
  </section>
  <ExampleImport @imported="store.load" />
  <div class="list-toolbar">
    <button
      class="compact-button"
      :disabled="store.loading"
      @click="store.load"
    >
      刷新列表
    </button>
  </div>
  <p v-if="store.error" class="error" role="alert">{{ store.error }}</p>
  <div v-else-if="store.loading" class="empty-state">正在加载项目…</div>
  <div v-else-if="!store.projects.length" class="empty-state">
    <h2>还没有项目</h2>
    <p>选择“创建项目”，开始整理业务数据与文档。</p>
  </div>
  <div v-else class="project-list">
    <table class="project-table" aria-label="项目列表">
      <thead>
        <tr>
          <th scope="col">项目名称</th>
          <th scope="col">描述</th>
          <th scope="col">更新时间</th>
          <th scope="col" class="align-right">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="project in store.projects" :key="project.id">
          <td class="project-name">
            <RouterLink :to="`/projects/${project.id}`">{{
              project.name
            }}</RouterLink>
          </td>
          <td class="project-description">{{ project.description || "—" }}</td>
          <td class="project-date">{{ dateLabel(project.updated_at) }}</td>
          <td class="align-right">
            <RouterLink
              class="button compact-button"
              :to="`/projects/${project.id}`"
              :aria-label="`打开项目：${project.name}`"
              >打开</RouterLink
            >
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
