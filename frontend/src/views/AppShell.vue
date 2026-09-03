<template>
  <div>
    <header class="wrap row" style="justify-content: space-between">
      <strong><router-link to="/app" style="color: inherit">CFB Sicko</router-link></strong>
      <nav class="row muted">
        <router-link to="/app">Picks</router-link>
        <router-link to="/app/standings">Standings</router-link>
        <router-link v-if="me?.is_commish" to="/app/admin">Admin</router-link>
        <button v-if="token" class="ghost" type="button" @click="logout">Out</button>
      </nav>
    </header>
    <SignIn v-if="!token && ready" @authed="refresh" />
    <p v-else-if="error" class="wrap muted">{{ error }}</p>
    <router-view v-else-if="token" :token="token" :me="me" />
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import SignIn from "../components/SignIn.vue";
import { api } from "../api.js";
import { getToken, signOut } from "../session.js";

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
    error.value = exc.status === 403 ? "You need an invite before you can see the league." : exc.message;
  }
  ready.value = true;
}

async function logout() {
  await signOut();
  token.value = null;
  me.value = null;
}

onMounted(refresh);
</script>
