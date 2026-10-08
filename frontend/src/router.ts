import { createRouter, createWebHistory } from "vue-router";
import ProjectsView from "./views/ProjectsView.vue";
import ProjectView from "./views/ProjectView.vue";
import AnalysisView from "./views/AnalysisView.vue";
import AgentView from "./views/AgentView.vue";
import AgentRunView from "./views/AgentRunView.vue";
import AgentHistoryView from "./views/AgentHistoryView.vue";

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: ProjectsView },
    { path: "/projects/:id", component: ProjectView },
    { path: "/projects/:id/analysis", component: AnalysisView },
    { path: "/projects/:id/agent", component: AgentView },
    { path: "/projects/:id/agent/history", component: AgentHistoryView },
    { path: "/projects/:id/agent/runs/:runId", component: AgentRunView },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
});
