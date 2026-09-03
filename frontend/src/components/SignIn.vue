<template>
  <section class="wrap card">
    <h2>Sign in</h2>
    <p class="muted">
      We email a 6-digit code. Type it here. Do not click the link in the email — Proton and some
      scanners burn it.
    </p>
    <form v-if="!sent" class="row" @submit.prevent="send">
      <input v-model="email" type="email" required placeholder="you@school.edu" />
      <button type="submit" :disabled="busy">Send code</button>
    </form>
    <form v-else class="row" @submit.prevent="confirm">
      <input v-model="code" inputmode="numeric" autocomplete="one-time-code" required placeholder="123456" />
      <button type="submit" :disabled="busy || code.trim().length < 6">Enter</button>
      <button class="ghost" type="button" :disabled="busy" @click="sent = false">Resend</button>
    </form>
    <p v-if="note" class="muted">{{ note }}</p>
  </section>
</template>

<script setup>
import { ref } from "vue";
import { signIn, verifyEmailCode } from "../session.js";

const emit = defineEmits(["authed"]);
const email = ref("");
const code = ref("");
const sent = ref(false);
const busy = ref(false);
const note = ref("");

async function send() {
  busy.value = true;
  note.value = "";
  try {
    await signIn(email.value);
    sent.value = true;
    note.value = "Code sent. Use the 6 digits in the email, not the button.";
  } catch (exc) {
    note.value = exc.message || String(exc);
  } finally {
    busy.value = false;
  }
}

async function confirm() {
  busy.value = true;
  note.value = "";
  try {
    await verifyEmailCode(email.value, code.value);
    emit("authed");
  } catch (exc) {
    note.value = exc.message || String(exc);
  } finally {
    busy.value = false;
  }
}
</script>
