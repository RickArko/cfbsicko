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
      auth: {
        persistSession: true,
        detectSessionInUrl: true,
        flowType: "implicit",
      },
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
    options: { emailRedirectTo: redirectTo, shouldCreateUser: true },
  });
  if (error) throw error;
}

export async function verifyEmailCode(email, token) {
  const client = await getClient();
  if (!client) throw new Error("Auth is not configured");
  const { error } = await client.auth.verifyOtp({
    email,
    token: token.trim(),
    type: "email",
  });
  if (error) throw error;
}

export function hashAuthError() {
  const raw = window.location.hash.replace(/^#/, "");
  const params = new URLSearchParams(raw);
  const code = params.get("error_code") || params.get("error");
  if (!code) return "";
  if (code === "otp_expired") {
    return "That email link was already used or scanned (ProtonMail does this). Request a new code and type the 6 digits — do not click the link.";
  }
  return params.get("error_description")?.replace(/\+/g, " ") || code;
}

export async function signOut() {
  const client = await getClient();
  if (client) await client.auth.signOut();
}
