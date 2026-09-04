<template>
  <main class="wrap">
    <p v-if="loadError" class="muted">{{ loadError }}</p>
    <template v-else-if="week">
      <div class="row" style="justify-content: space-between">
        <h1 style="margin: 0">{{ week.title }}</h1>
        <span class="pill">{{ locked ? "Locked" : countdown }}</span>
      </div>
      <p class="muted">Exactly five. Spreads or totals. Use the listed number.</p>
      <div class="row">
        <button
          v-for="day in days"
          :key="day"
          type="button"
          :class="dayFilter === day ? '' : 'ghost'"
          @click="dayFilter = dayFilter === day ? '' : day"
        >
          {{ day }}
        </button>
      </div>
      <article v-for="game in visibleGames" :key="game.id" class="game">
        <div>
          <strong>{{ game.away }} at {{ game.home }}</strong>
          <div class="muted">{{ favorite(game) }} · O/U {{ game.total }}</div>
          <div v-if="moved(game)" class="muted">Market now {{ marketLine(game) }} — lock stays the listed number.</div>
          <div v-if="game.game_status" class="muted">
            {{ game.game_status }}{{ scoreLine(game) }}
          </div>
        </div>
        <div class="row">
          <button type="button" :class="selected(game, 'spread', 'away') ? '' : 'ghost'" :disabled="locked" @click="toggle(game, 'spread', 'away')">
            {{ game.away }} {{ awaySpread(game) }}
          </button>
          <button type="button" :class="selected(game, 'spread', 'home') ? '' : 'ghost'" :disabled="locked" @click="toggle(game, 'spread', 'home')">
            {{ game.home }} {{ homeSpread(game) }}
          </button>
          <button type="button" :class="selected(game, 'total', 'over') ? '' : 'ghost'" :disabled="locked" @click="toggle(game, 'total', 'over')">
            Over {{ game.total }}
          </button>
          <button type="button" :class="selected(game, 'total', 'under') ? '' : 'ghost'" :disabled="locked" @click="toggle(game, 'total', 'under')">
            Under {{ game.total }}
          </button>
        </div>
      </article>
    </template>
    <div class="tray row" style="justify-content: space-between">
      <span>{{ picks.length }}/5 selected</span>
      <button type="button" :disabled="locked || picks.length !== 5 || saving" @click="save">
        {{ saving ? "Saving…" : "Save picks" }}
      </button>
    </div>
    <p v-if="pickResults.length" class="wrap muted">{{ pickResults }}</p>
    <p v-if="note" class="wrap muted">{{ note }}</p>
  </main>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api } from "../api.js";

const props = defineProps({ token: String, me: Object });
const week = ref(null);
const games = ref([]);
const picks = ref([]);
const locked = ref(false);
const dayFilter = ref("");
const loadError = ref("");
const note = ref("");
const saving = ref(false);
const now = ref(Date.now());
let timer;

const days = computed(() => [...new Set(games.value.map((g) => g.day_label))]);
const visibleGames = computed(() =>
  dayFilter.value ? games.value.filter((g) => g.day_label === dayFilter.value) : games.value,
);

const savedPicks = ref([]);

const pickResults = computed(() => {
  const rows = savedPicks.value.filter((p) => p.result && p.result !== "pending");
  if (!rows.length) return "";
  return rows.map((p) => `${p.away || ""} ${p.result}`).join(" · ");
});

const countdown = computed(() => {
  if (!week.value?.lock_at) return "";
  const ms = new Date(week.value.lock_at).getTime() - now.value;
  if (ms <= 0) return "Locked";
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  return `${h}h ${m}m to lock`;
});

function homeSpread(game) {
  const n = game.spread_home;
  return `${n > 0 ? "+" : ""}${n}`;
}
function awaySpread(game) {
  const n = -game.spread_home;
  return `${n > 0 ? "+" : ""}${n}`;
}
function favorite(game) {
  return game.spread_home <= 0 ? `${game.home} ${game.spread_home}` : `${game.away} ${-game.spread_home}`;
}
function moved(game) {
  const ms = game.market_spread_home;
  const mt = game.market_total;
  if (ms == null || mt == null) return false;
  return Math.abs(ms - game.spread_home) >= 0.5 || Math.abs(mt - game.total) >= 0.5;
}
function marketLine(game) {
  return `${favorite({ ...game, spread_home: game.market_spread_home })} · O/U ${game.market_total}`;
}
function scoreLine(game) {
  if (game.away_score == null || game.home_score == null) return "";
  return ` ${game.away_score}–${game.home_score}`;
}
function selected(game, market, side) {
  return picks.value.some((p) => p.game_id === game.id && p.market === market && p.side === side);
}
function toggle(game, market, side) {
  const idx = picks.value.findIndex((p) => p.game_id === game.id && p.market === market);
  if (idx >= 0 && picks.value[idx].side === side) {
    picks.value.splice(idx, 1);
    return;
  }
  if (idx >= 0) {
    picks.value[idx] = { game_id: game.id, market, side };
    return;
  }
  if (picks.value.length >= 5) {
    note.value = "Drop one pick before adding another.";
    return;
  }
  picks.value.push({ game_id: game.id, market, side });
}

async function load() {
  try {
    const data = await api("/api/weeks/current", { token: props.token });
    week.value = data.week;
    games.value = data.games;
    locked.value = data.locked;
    savedPicks.value = data.my_picks || [];
    picks.value = savedPicks.value.map((p) => ({
      game_id: p.game_id,
      market: p.market,
      side: p.side,
    }));
  } catch (exc) {
    loadError.value = exc.message;
  }
}

async function save() {
  saving.value = true;
  note.value = "";
  try {
    const body = {
      picks: picks.value.map((p, i) => ({ ...p, slot: i + 1 })),
    };
    await api("/api/weeks/current/picks", { method: "PUT", token: props.token, body });
    note.value = "Saved.";
  } catch (exc) {
    note.value = typeof exc.data?.detail === "string" ? exc.data.detail : exc.message;
  } finally {
    saving.value = false;
  }
}

onMounted(() => {
  load();
  timer = setInterval(() => {
    now.value = Date.now();
    load();
  }, 30000);
});
onUnmounted(() => clearInterval(timer));
</script>
