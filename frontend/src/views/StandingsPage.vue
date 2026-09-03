<template>
  <main class="wrap">
    <h1>{{ data?.league?.name || "Standings" }}</h1>
    <p v-if="data" class="muted">
      Buy-in ${{ data.payout.buy_in || 75 }} · pot ${{ data.payout.pot }} · 1st ${{ data.payout.first }}
      · 2nd ${{ data.payout.second }} · 3rd ${{ data.payout.third }} (paid only). Bottom
      {{ data.payout.bottom_n || 3 }} each owe ${{ data.payout.extra_each_bottom }} extra.
    </p>
    <table v-if="data">
      <thead>
        <tr>
          <th>#</th>
          <th>Name</th>
          <th>W-T-L</th>
          <th>Paid</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in data.table" :key="row.user_id">
          <td>{{ row.rank }}</td>
          <td>{{ row.display_name }}</td>
          <td>{{ row.record }}</td>
          <td>{{ row.buy_in_paid ? "yes" : "—" }}</td>
        </tr>
      </tbody>
    </table>
    <p v-if="error" class="muted">{{ error }}</p>
  </main>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { api } from "../api.js";

const props = defineProps({ token: String, me: Object });
const data = ref(null);
const error = ref("");

onMounted(async () => {
  try {
    data.value = await api("/api/standings", { token: props.token });
  } catch (exc) {
    error.value = exc.message;
  }
});
</script>
