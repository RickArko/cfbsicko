<template>
  <section class="wrap card">
    <h2>Sign in</h2>
    <p class="muted">We’ll email you a magic link. Use the address the commissioner invited.</p>
    <form class="row" @submit.prevent="submit">
      <input v-model="email" type="email" required placeholder="you@school.edu" />
      <button type="submit" :disabled="busy">Send link</button>
    </form>
    <p v-if="note" class="muted">{{ note }}</p>
  </section>
</template>

<script setup>
import { ref } from "vue";
import { signIn } from "../session.js";

const emit = defineEmits(["authed"]);
const email = ref("");
const busy = ref(false);
const note = ref("");

async function submit() {
  busy.value = true;
  note.value = "";
  try {
    await signIn(email.value);
    note.value = "Check your inbox. After you click the link, come back here.";
    emit("authed");
  } catch (exc) {
    note.value = exc.message || String(exc);
  } finally {
    busy.value = false;
  }
}
</script>
