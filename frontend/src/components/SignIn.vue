<template>
  <section class="wrap card auth-card">
    <p class="eyebrow">Invite only</p>
    <h2>Enter the league</h2>
    <form v-if="localLogin" class="row" @submit.prevent="passwordLogin">
      <p class="muted" style="flex-basis: 100%">
        Temporary password login (test user). Turn this off once email codes work.
      </p>
      <input v-model="email" type="email" required />
      <input v-model="password" type="password" required placeholder="password" />
      <button type="submit" :disabled="busy">Sign in</button>
    </form>
    <p class="muted">
      We email a 6-digit code. Type it here. If you use ProtonMail, do not tap the link.
    </p>
    <form v-if="!sent" class="row" @submit.prevent="send">
      <input v-if="!localLogin" v-model="email" type="email" required placeholder="you@school.edu" />
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
import { onMounted, ref } from "vue";
import { loadConfig, localPasswordLogin, signIn, verifyEmailCode } from "../session.js";

const emit = defineEmits(["authed"]);
const email = ref("");
const password = ref("");
const code = ref("");
const sent = ref(false);
const busy = ref(false);
const note = ref("");
const localLogin = ref(false);

onMounted(async () => {
  try {
    const cfg = await loadConfig();
    localLogin.value = Boolean(cfg.local_login);
    if (cfg.test_email) email.value = cfg.test_email;
  } catch {
    note.value = "Auth is unreachable. Try again in a moment.";
  }
});

async function passwordLogin() {
  busy.value = true;
  note.value = "";
  try {
    await localPasswordLogin(email.value, password.value);
    emit("authed");
  } catch (exc) {
    note.value = exc.message || String(exc);
  } finally {
    busy.value = false;
  }
}

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
