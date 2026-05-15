/**
 * One-time .env → settings migration. Runs at bootstrap, no-op when
 * `installed` is already true.
 *
 * Reads the legacy TEMPER_* / LLM_* / SMITH_* env vars (the shape
 * config.ts v1 used) and writes them into the settings table.
 * If a critical pair is present (LLM_PROVIDER + LLM_API_KEY +
 * LLM_MODEL + TEMPER_API_KEY), marks the install complete so the
 * user can skip the wizard.
 *
 * If env is partial (e.g. only TEMPER_API_KEY): import what's there
 * and DON'T mark installed; the user goes through the wizard which
 * pre-fills the missing pieces.
 */
import {
  isInstalled,
  markInstalled,
  setSecretSetting,
  setSetting,
} from "./settings.js";
import { SETTING_KEYS } from "../config.js";

interface ImportResult {
  imported: number;
  installed: boolean;
}

export function migrateEnvSettings(): ImportResult {
  if (isInstalled()) {
    return { imported: 0, installed: true };
  }

  const env = process.env;
  let n = 0;

  function pushStr(key: string, value: string | undefined, desc?: string): boolean {
    if (typeof value === "string" && value.trim()) {
      setSetting(key, value.trim(), { description: desc, updatedBy: "env-migration" });
      n++;
      return true;
    }
    return false;
  }

  function pushNum(key: string, value: string | undefined, desc?: string): boolean {
    const v = Number((value ?? "").trim());
    if (Number.isFinite(v) && v >= 0 && value && value.trim()) {
      setSetting(key, v, { description: desc, updatedBy: "env-migration" });
      n++;
      return true;
    }
    return false;
  }

  function pushBool(key: string, value: string | undefined, desc?: string): boolean {
    if (typeof value === "string" && value.trim()) {
      const v = /^(1|true|yes)$/i.test(value.trim());
      setSetting(key, v, { description: desc, updatedBy: "env-migration" });
      n++;
      return true;
    }
    return false;
  }

  function pushSecret(key: string, value: string | undefined, desc?: string): boolean {
    if (typeof value === "string" && value.trim()) {
      setSecretSetting(key, value.trim(), { description: desc, updatedBy: "env-migration" });
      n++;
      return true;
    }
    return false;
  }

  // Non-secret strings + numbers.
  const haveAgentSlug = pushStr(SETTING_KEYS.smithAgentSlug, env.SMITH_AGENT_SLUG);
  if (!haveAgentSlug) pushStr(SETTING_KEYS.smithAgentSlug, "smith");  // default
  const haveTemperUrl = pushStr(SETTING_KEYS.temperBaseUrl, env.TEMPER_BASE_URL);
  if (!haveTemperUrl) pushStr(SETTING_KEYS.temperBaseUrl, "http://127.0.0.1:18088");

  const haveLlmProvider = pushStr(SETTING_KEYS.llmProvider, env.LLM_PROVIDER);
  const haveLlmModel = pushStr(SETTING_KEYS.llmModel, env.LLM_MODEL);
  pushStr(SETTING_KEYS.llmBaseUrl, env.LLM_BASE_URL);

  pushNum(SETTING_KEYS.consolidateScheduleHours, env.CONSOLIDATE_SCHEDULE_HOURS);
  pushBool(SETTING_KEYS.consolidateAutoApply, env.CONSOLIDATE_AUTO_APPLY);
  pushStr("recall.log_level", env.SMITH_RECALL_LOG);

  // Secrets.
  const haveTemperKey = pushSecret(SETTING_KEYS.temperApiKey, env.TEMPER_API_KEY);
  const haveLlmKey = pushSecret(SETTING_KEYS.llmApiKey, env.LLM_API_KEY);
  pushSecret(SETTING_KEYS.smithSecret, env.SMITH_SECRET);

  // If all the critical pieces are present, mark install complete —
  // the user already had a working .env setup. Otherwise leave
  // installed=false so the wizard prompts for the rest.
  const allEssentials = haveTemperUrl && haveTemperKey && haveLlmProvider && haveLlmModel && haveLlmKey;
  if (allEssentials) {
    markInstalled("env-migration");
    console.log(
      `[smith.settings] imported ${n} value(s) from .env and marked installed. ` +
      `You can now remove TEMPER_*, LLM_*, CONSOLIDATE_*, SMITH_RECALL_LOG, ` +
      `SMITH_AGENT_SLUG, SMITH_SECRET from .env — they're served from .data/smith.db.`,
    );
    return { imported: n, installed: true };
  }

  if (n > 0) {
    console.log(
      `[smith.settings] imported ${n} value(s) from .env into settings table. ` +
      `Critical values still missing — visit /setup to complete configuration.`,
    );
  }
  return { imported: n, installed: false };
}
