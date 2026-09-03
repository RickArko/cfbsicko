<template>
  <div class="landing">
    <a class="skip-link" href="#main">Skip to content</a>
    <header class="site-header">
      <nav class="site-nav wrap" aria-label="Main">
        <a class="brand" href="/" aria-label="CFB Sicko home">
          <span class="brand-lead">CFB</span><span class="brand-rest"> Sicko</span>
        </a>
        <div class="nav-actions">
          <router-link class="btn ghost" to="/app">Sign in</router-link>
          <router-link class="btn primary" to="/app">Lock your five</router-link>
        </div>
      </nav>
    </header>

    <main id="main">
      <section class="hero wrap">
        <div class="hero-copy">
          <p class="eyebrow">Private locks league · 2026</p>
          <h1>Five locks. One Thursday. <span class="accent">No spreadsheet.</span></h1>
          <p class="lede">
            Frozen Tuesday lines. Exactly five spreads or totals. The board stays dark until lock.
          </p>
          <div class="hero-ctas">
            <router-link class="btn primary lg" to="/app">Lock your five</router-link>
            <a class="btn ghost lg" href="#how-it-works">How a week works</a>
          </div>
          <ul class="proof">
            <li>
              <strong>Exactly five</strong>
              <span>spreads or totals, your mix</span>
            </li>
            <li>
              <strong>Frozen Tuesday lines</strong>
              <span>the listed number is the number</span>
            </li>
            <li>
              <strong>Thursday 6pm ET</strong>
              <span>hidden until lock</span>
            </li>
          </ul>
        </div>

        <article class="locks-card card" aria-hidden="true">
          <div class="locks-card-head">
            <strong>Week 1</strong>
            <span class="pill pulse">Open · 18h to lock</span>
          </div>
          <p class="muted locks-card-lede">Exactly five. Spreads or totals.</p>
          <ul class="lock-slots">
            <li>Houston −20.5</li>
            <li>Purdue / Iowa State Under 57.5</li>
            <li>Washington State +23.5</li>
            <li class="empty">Lock 4</li>
            <li class="empty">Lock 5</li>
          </ul>
          <div class="locks-card-foot">
            <span>3/5 selected</span>
            <button type="button" disabled>Save picks</button>
          </div>
        </article>
      </section>

      <section id="how-it-works" class="band wrap reveal">
        <h2>Tuesday lines. Thursday locks. Sunday truth.</h2>
        <ol class="steps">
          <li>
            <span class="step-num">1</span>
            <h3>Commish posts the slate</h3>
            <p class="muted">Tuesday the listed numbers go up. That is the only legal line all week.</p>
          </li>
          <li>
            <span class="step-num">2</span>
            <h3>You lock exactly five</h3>
            <p class="muted">Any mix of spreads and totals. Window closes Thursday 6pm ET.</p>
          </li>
          <li>
            <span class="step-num">3</span>
            <h3>The board grades itself</h3>
            <p class="muted">ATS and totals vs the frozen number. Push is a tie. Standings update.</p>
          </li>
        </ol>
      </section>

      <section class="band wrap reveal">
        <h2>Built for the group chat, not a book.</h2>
        <div class="feature-grid">
          <article class="card">
            <h3>The board stays dark</h3>
            <p class="muted">Nobody sees your card until lock. Same for everyone.</p>
          </article>
          <article class="card">
            <h3>Running W-T-L</h3>
            <p class="muted">Season table plus pot preview for paid players. No spreadsheet math.</p>
          </article>
          <article class="card">
            <h3>The listed number</h3>
            <p class="muted">Market moves do not matter. Use the number on the slate.</p>
          </article>
        </div>
      </section>

      <section class="band wrap reveal">
        <h2>House rules</h2>
        <article class="card charter">
          <p>$75 buy-in. Winner 60%, second 30%, third 10%. Bottom three each owe another $75 to one of the top three.</p>
          <p>Exactly five picks — any mix of spreads and totals — against the lines the commissioner publishes.</p>
          <p>FBS vs FCS counts. Conference championships and Army-Navy do not. 2026 regular season only.</p>
        </article>
      </section>

      <section class="cta-band wrap reveal">
        <h2>The sheet is dead. The league is here.</h2>
        <p class="lede">
          Invited? Use the email on the list. Everyone else: there is no public signup.
        </p>
        <router-link class="btn primary lg" to="/app">Lock your five</router-link>
      </section>
    </main>

    <footer class="site-footer wrap">
      <router-link to="/app">Sign in</router-link>
      <span class="muted">2026 regular season</span>
    </footer>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted } from "vue";
import { useRouter } from "vue-router";
import { getToken } from "../session.js";

const router = useRouter();
let observer;

onMounted(async () => {
  try {
    const token = await getToken();
    if (token) {
      await router.replace("/app");
      return;
    }
  } catch {
    /* show landing if auth config is down */
  }

  const nodes = document.querySelectorAll(".landing .reveal");
  if (!nodes.length) return;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    nodes.forEach((node) => node.classList.add("is-visible"));
    return;
  }
  observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      }
    },
    { threshold: 0.12 },
  );
  nodes.forEach((node) => observer.observe(node));
});

onUnmounted(() => {
  observer?.disconnect();
});
</script>
