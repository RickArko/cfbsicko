<template>
  <main class="wrap">
    <h1>Commissioner</h1>
    <p class="muted">{{ currentLeagueName }} · same Tuesday slate, own pot and standings.</p>
    <section class="card" style="margin-bottom: 1rem">
      <h2>New league</h2>
      <form class="row" @submit.prevent="createLeague">
        <input v-model="lgName" required placeholder="Office pool" />
        <input v-model.number="lgBuyIn" type="number" min="1" style="max-width: 6rem" />
        <button type="submit">Create</button>
      </form>
    </section>
    <section class="card" style="margin-bottom: 1rem">
      <h2>Invite</h2>
      <form class="row" @submit.prevent="invite">
        <input v-model="invEmail" type="email" required placeholder="email" />
        <input v-model="invName" placeholder="display name (Stu)" />
        <button type="submit">Invite</button>
      </form>
    </section>
    <section class="card" style="margin-bottom: 1rem">
      <h2>Publish slate</h2>
      <p class="muted">Paste the Tuesday email. Same format as Week 1.</p>
      <div class="row">
        <input v-model.number="weekNo" type="number" min="1" style="max-width: 6rem" />
        <input v-model="lockAt" placeholder="2026-09-10T18:00:00-04:00" />
      </div>
      <textarea v-model="slate" rows="10" style="margin: 0.6rem 0"></textarea>
      <button type="button" @click="publish">Publish</button>
    </section>
    <section class="card" style="margin-bottom: 1rem">
      <h2>Grade</h2>
      <div v-for="game in games" :key="game.id" class="row" style="margin-bottom: 0.4rem">
        <span style="flex: 1">{{ game.away }} at {{ game.home }}</span>
        <input v-model.number="scores[game.id].away" type="number" placeholder="away" style="max-width: 5rem" />
        <input v-model.number="scores[game.id].home" type="number" placeholder="home" style="max-width: 5rem" />
        <button class="ghost" type="button" @click="saveScore(game)">Save</button>
      </div>
      <div class="row">
        <button type="button" @click="grade">Grade week</button>
        <button class="ghost" type="button" @click="mail('slate')">Email slate</button>
        <button class="ghost" type="button" @click="mail('reminder')">Remind missing</button>
        <button class="ghost" type="button" @click="mail('standings')">Email standings</button>
      </div>
    </section>
    <section class="card" style="margin-bottom: 1rem">
      <h2>Paid</h2>
      <div v-for="u in users" :key="u.id" class="row">
        <span style="flex: 1">{{ u.display_name }}</span>
        <button class="ghost" type="button" @click="togglePaid(u)">{{ u.buy_in_paid ? "paid" : "unpaid" }}</button>
      </div>
    </section>
    <section class="card">
      <h2>Snapshots</h2>
      <p v-for="s in snapshots" :key="s.id">
        <a :href="`/api/admin/snapshots/${s.id}`" @click.prevent="download(s)">{{ s.kind }} · {{ s.created_at }}</a>
      </p>
    </section>
    <p class="muted">{{ note }}</p>
  </main>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { api } from "../api.js";

const props = defineProps({ token: String, me: Object });
const invEmail = ref("");
const invName = ref("");
const lgName = ref("");
const lgBuyIn = ref(75);
const currentLeagueName = ref("CFB Sicko");
const weekNo = ref(2);
const lockAt = ref("2026-09-10T18:00:00-04:00");
const slate = ref("");
const games = ref([]);
const scores = reactive({});
const users = ref([]);
const snapshots = ref([]);
const note = ref("");

function auth() {
  return { token: props.token };
}

async function load() {
  try {
    const week = await api("/api/weeks/current", auth());
    games.value = week.games;
    weekNo.value = week.week.week_no;
    lockAt.value = week.week.lock_at;
    for (const g of week.games) {
      scores[g.id] = { home: g.home_score, away: g.away_score };
    }
  } catch {
    games.value = [];
  }
  users.value = (await api("/api/admin/users", auth())).users;
  snapshots.value = (await api("/api/admin/snapshots", auth())).snapshots;
  if (props.me?.league?.name) currentLeagueName.value = props.me.league.name;
}

async function createLeague() {
  const created = await api("/api/admin/leagues", {
    method: "POST",
    token: props.token,
    body: { name: lgName.value, buy_in: lgBuyIn.value },
  });
  note.value = `Created ${created.name} ($${created.buy_in}). Switch to it in the header to invite and mark paid.`;
  lgName.value = "";
}

async function invite() {
  const r = await api("/api/admin/invites", {
    method: "POST",
    token: props.token,
    body: { email: invEmail.value, display_name: invName.value || null },
  });
  note.value = r.mailed
    ? `Invited ${invEmail.value} — welcome mail sent.`
    : `Invited ${invEmail.value} (mail did not send).`;
  invEmail.value = "";
}

async function publish() {
  await api("/api/admin/weeks", {
    method: "POST",
    token: props.token,
    body: { week_no: weekNo.value, lock_at: lockAt.value, slate_text: slate.value },
  });
  note.value = "Slate published.";
  await load();
}

async function saveScore(game) {
  const s = scores[game.id];
  await api(`/api/admin/games/${game.id}/result`, {
    method: "PUT",
    token: props.token,
    body: { home_score: Number(s.home), away_score: Number(s.away) },
  });
  note.value = `Saved ${game.home}`;
}

async function grade() {
  await api(`/api/admin/weeks/${weekNo.value}/grade`, { method: "POST", token: props.token });
  note.value = "Graded.";
  await load();
}

async function mail(kind) {
  const r = await api(`/api/admin/weeks/${weekNo.value}/mail/${kind}`, { method: "POST", token: props.token });
  note.value = `Sent ${r.sent}`;
}

async function togglePaid(u) {
  await api(`/api/admin/users/${u.id}`, {
    method: "PATCH",
    token: props.token,
    body: { buy_in_paid: !u.buy_in_paid },
  });
  await load();
}

async function download(s) {
  const data = await api(`/api/admin/snapshots/${s.id}`, auth());
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `cfbsicko-week-${s.week_id}-${s.kind}.json`;
  a.click();
}

onMounted(load);
</script>
