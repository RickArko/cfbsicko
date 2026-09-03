<template>
  <main class="wrap">
    <h1>Week {{ $route.params.n }}</h1>
    <p v-if="error" class="muted">{{ error }}</p>
    <section v-for="row in board" :key="row.user_id" class="card" style="margin-bottom: 0.8rem">
      <strong>{{ row.display_name }}</strong>
      <ol>
        <li v-for="pick in row.picks" :key="pick.id">
          {{ pickLabel(pick) }}
          <span v-if="pick.result !== 'pending'" :class="klass(pick.result)"> {{ pick.result }}</span>
        </li>
      </ol>
    </section>
  </main>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { api, pickLabel } from "../api.js";

const props = defineProps({ token: String, me: Object });
const route = useRoute();
const board = ref([]);
const error = ref("");

function klass(result) {
  if (result === "W") return "win";
  if (result === "L") return "loss";
  return "tie";
}

onMounted(async () => {
  try {
    const data = await api(`/api/weeks/${route.params.n}/board`, { token: props.token });
    board.value = data.board;
  } catch (exc) {
    error.value = exc.message;
  }
});
</script>
