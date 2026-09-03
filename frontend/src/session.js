import { createClient } from "@supabase/supabase-js";
import { api } from "./api.js";

let supabase = null;
let config = null;

export async function loadConfig() {
  if (!config) config = await api("/api/auth/config");
  return config;
}

export async function getClient() {
  const cfg = await loadConfig();
  if (!cfg.supabase_url || !cfg.supabase_anon_key) return null;
  if (!supabase) {
    supabase = createClient(cfg.supabase_url, cfg.supabase_anon_key, {
      auth: { persistSession: true, detectSessionInUrl: true },
    });
  }
  return supabase;
}

export async function getToken() {
  const client = await getClient();
  if (!client) return null;
  const { data } = await client.auth.getSession();
  return data.session?.access_token || null;
}

export async function signIn(email) {
  const client = await getClient();
  if (!client) throw new Error("Auth is not configured");
  const redirectTo = `${window.location.origin}/app`;
  const { error } = await client.auth.signInWithOtp({
    email,
    options: { emailRedirectTo: redirectTo },
  });
  if (error) throw error;
}

export async function signOut() {
  const client = await getClient();
  if (client) await client.auth.signOut();
}
