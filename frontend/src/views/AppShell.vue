<template>
  <div>
    <header class="site-header">
      <nav class="site-nav wrap" aria-label="League">
        <router-link class="brand" to="/app" aria-label="CFB Sicko picks">
          <span class="brand-lead">CFB</span><span class="brand-rest"> Sicko</span>
        </router-link>
        <div class="nav-actions">
          <template v-if="token && me">
            <select
              v-if="me.leagues && me.leagues.length"
              class="league-switch"
              aria-label="Active league"
              :value="leagueId || me.league?.id"
              @change="switchLeague"
            >
              <option v-for="lg in me.leagues" :key="lg.id" :value="lg.id">{{ lg.name }}</option>
            </select>
            <router-link to="/app">Picks</router-link>
            <router-link to="/app/standings">Standings</router-link>
            <router-link v-if="me.is_commish" to="/app/admin">Admin</router-link>
          </template>
          <button v-if="token" class="ghost" type="button" @click="logout">Out</button>
        </div>
      </nav>
    </header>
    <section v-if="denied" class="wrap card auth-card">
      <p class="eyebrow">Invite only</p>
      <h2>Closed door</h2>
      <p>{{ denied }}</p>
      <button class="ghost" type="button" @click="logout">Use a different email</button>
    </section>
    <template v-else-if="!token">
      <p v-if="hashNote" class="wrap muted">{{ hashNote }}</p>
      <SignIn @authed="refresh" />
    </template>
    <p v-else-if="!me" class="wrap muted">Entering the league…</p>
    <router-view
      v-else
      :key="leagueId || me.league?.id"
      :token="token"
      :me="me"
      @league-changed="onLeagueChanged"
    />
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import SignIn from "../components/SignIn.vue";
import { api, getLeagueId, setLeagueId } from "../api.js";
import { getToken, hashAuthError, signOut } from "../session.js";

const token = ref(null);
const me = ref(null);
const leagueId = ref(getLeagueId());
const denied = ref("");
const hashNote = ref("");

async function refresh() {
  denied.value = "";
  try {
    token.value = await getToken();
  } catch {
    token.value = null;
    me.value = null;
    return;
  }
  if (!token.value) {
    me.value = null;
    return;
  }
  try {
    me.value = await api("/api/me", { token: token.value });
    if (!leagueId.value && me.value.league?.id) {
      leagueId.value = me.value.league.id;
      setLeagueId(leagueId.value);
    }
  } catch (exc) {
    if (exc.status === 403 && getLeagueId()) {
      setLeagueId(null);
      leagueId.value = null;
      try {
        me.value = await api("/api/me", { token: token.value });
        if (me.value.league?.id) {
          leagueId.value = me.value.league.id;
          setLeagueId(leagueId.value);
        }
        return;
      } catch (retry) {
        denied.value =
          retry.status === 403
            ? "That email is not on this year’s list. Ask the commissioner."
            : retry.message;
        return;
      }
    }
    denied.value =
      exc.status === 403
        ? "That email is not on this year’s list. Ask the commissioner."
        : exc.message;
  }
}

async function onLeagueChanged(id) {
  const n = Number(id);
  if (!Number.isFinite(n) || n < 1) return;
  leagueId.value = n;
  setLeagueId(n);
  await refresh();
}

async function switchLeague(event) {
  await onLeagueChanged(event.target.value);
}

async function logout() {
  await signOut();
  setLeagueId(null);
  leagueId.value = null;
  token.value = null;
  me.value = null;
  denied.value = "";
}

onMounted(async () => {
  const fromHash = hashAuthError();
  if (fromHash) {
    hashNote.value = fromHash;
    history.replaceState(null, "", window.location.pathname);
  }
  await refresh();
});
</script>
