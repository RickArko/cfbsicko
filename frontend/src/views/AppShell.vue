<template>
  <div>
    <header class="site-header">
      <nav class="site-nav wrap" aria-label="League">
        <router-link class="brand" to="/app" aria-label="CFB Sicko picks">
          <span class="brand-lead">CFB</span><span class="brand-rest"> Sicko</span>
        </router-link>
        <div class="nav-actions">
          <template v-if="token && me">
            <router-link to="/app">Picks</router-link>
            <router-link to="/app/standings">Standings</router-link>
            <router-link v-if="me.is_commish" to="/app/admin">Admin</router-link>
          </template>
          <button v-if="token" class="ghost" type="button" @click="logout">Out</button>
        </div>
      </nav>
    </header>
    <SignIn v-if="!token && ready" @authed="refresh" />
    <section v-else-if="error" class="wrap card auth-card">
      <p class="eyebrow">Invite only</p>
      <h2>Closed door</h2>
      <p>{{ error }}</p>
      <button class="ghost" type="button" @click="logout">Use a different email</button>
    </section>
    <router-view v-else-if="token" :token="token" :me="me" />
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import SignIn from "../components/SignIn.vue";
import { api } from "../api.js";
import { getToken, hashAuthError, signOut } from "../session.js";

const token = ref(null);
const me = ref(null);
const ready = ref(false);
const error = ref("");

async function refresh() {
  error.value = "";
  token.value = await getToken();
  if (!token.value) {
    me.value = null;
    ready.value = true;
    return;
  }
  try {
    me.value = await api("/api/me", { token: token.value });
  } catch (exc) {
    error.value =
      exc.status === 403
        ? "That email is not on this year’s list. Ask the commissioner."
        : exc.message;
  }
  ready.value = true;
}

async function logout() {
  await signOut();
  token.value = null;
  me.value = null;
  error.value = "";
  ready.value = true;
}

onMounted(() => {
  const fromHash = hashAuthError();
  if (fromHash) {
    error.value = fromHash;
    history.replaceState(null, "", window.location.pathname);
  }
  refresh();
});
</script>
