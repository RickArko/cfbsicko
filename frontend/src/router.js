import { createRouter, createWebHistory } from "vue-router";
import LandingPage from "./views/LandingPage.vue";
import AppShell from "./views/AppShell.vue";
import PicksPage from "./views/PicksPage.vue";
import StandingsPage from "./views/StandingsPage.vue";
import WeekBoard from "./views/WeekBoard.vue";
import AdminPage from "./views/AdminPage.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: LandingPage },
    {
      path: "/app",
      component: AppShell,
      children: [
        { path: "", component: PicksPage },
        { path: "standings", component: StandingsPage },
        { path: "week/:n", component: WeekBoard },
        { path: "admin", component: AdminPage },
      ],
    },
  ],
});
