/**
 * SwarmClaw HF Space wake-on-request Worker
 *
 * Flow:
 *   1. Browser hits this Worker (your custom domain or *.workers.dev).
 *   2. Worker checks the Space status via HF API.
 *   3. If RUNNING, traffic is proxied straight through (with WebSocket support).
 *   4. If SLEEPING / PAUSED / STOPPED, the Worker calls the HF restart endpoint,
 *      then serves a tiny auto-refreshing "warming up" page.
 *   5. Once the Space is RUNNING, subsequent requests stream through normally.
 *
 * Notes:
 *   - HF Space direct URL pattern: https://{user}-{space}.hf.space
 *   - HF API:  https://huggingface.co/api/spaces/{user}/{space}
 *   - HF API:  POST .../restart    (requires HF_TOKEN with write on the Space)
 */

const RUNNING_STAGES = new Set(["RUNNING", "RUNNING_BUILDING", "RUNNING_APP_STARTING"]);
const WAKE_STAGES = new Set([
  "SLEEPING",
  "PAUSED",
  "STOPPED",
  "STOPPING",
  "BUILD_FAILED",
  "RUNTIME_ERROR",
  "CONFIG_ERROR",
]);

function spaceHost(env) {
  // HF normalizes "_" -> "-" in hostnames.
  const u = (env.HF_USERNAME || "").toLowerCase().replace(/_/g, "-");
  const s = (env.HF_SPACE || "").toLowerCase().replace(/_/g, "-");
  return `${u}-${s}.hf.space`;
}

function apiBase(env) {
  return `https://huggingface.co/api/spaces/${env.HF_USERNAME}/${env.HF_SPACE}`;
}

async function getStage(env) {
  const r = await fetch(apiBase(env), {
    headers: env.HF_TOKEN ? { Authorization: `Bearer ${env.HF_TOKEN}` } : {},
    cf: { cacheTtl: 0, cacheEverything: false },
  });
  if (!r.ok) return { stage: "UNKNOWN", httpStatus: r.status };
  const data = await r.json().catch(() => ({}));
  return { stage: data?.runtime?.stage || "UNKNOWN", httpStatus: r.status };
}

async function requestWake(env) {
  if (!env.HF_TOKEN) return { ok: false, reason: "no HF_TOKEN configured" };
  const r = await fetch(`${apiBase(env)}/restart`, {
    method: "POST",
    headers: { Authorization: `Bearer ${env.HF_TOKEN}` },
  });
  return { ok: r.ok, reason: r.ok ? "" : `restart returned ${r.status}` };
}

function gateRequest(request, env) {
  if ((env.REQUIRE_KEY || "false") !== "true") return null;
  const url = new URL(request.url);
  const provided =
    url.searchParams.get("key") ||
    (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  if (provided && env.ACCESS_KEY && provided === env.ACCESS_KEY) return null;
  return new Response("Unauthorized", { status: 401 });
}

function warmingPage(env, stage, waitSeconds) {
  const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>SwarmClaw is warming up…</title>
<meta name="viewport" content="width=device-width,initial-scale=1" />
<meta http-equiv="refresh" content="6" />
<style>
  :root { color-scheme: light dark; }
  body {
    font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    display: grid; place-items: center; min-height: 100vh; margin: 0;
    background: #0b0d10; color: #e7e9ee;
  }
  .card {
    max-width: 480px; padding: 32px 28px; border-radius: 14px;
    background: #14171c; border: 1px solid #232830; text-align: center;
  }
  h1 { margin: 0 0 8px; font-size: 20px; }
  p  { margin: 6px 0; color: #a8b0bd; font-size: 14px; }
  .stage { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
           font-size: 12px; color: #7fb069; }
  .spinner {
    width: 32px; height: 32px; margin: 12px auto 18px;
    border: 3px solid #2a2f38; border-top-color: #7fb069;
    border-radius: 50%; animation: spin 1s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
  <div class="card">
    <div class="spinner"></div>
    <h1>SwarmClaw is warming up</h1>
    <p>Your Hugging Face Space was asleep. We've asked it to come back.</p>
    <p>This page refreshes every 6 seconds. Cold starts usually take 30–90s.</p>
    <p class="stage">stage: ${stage} · waited: ${waitSeconds}s</p>
  </div>
</body>
</html>`;
  return new Response(html, {
    status: 503,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
      "retry-after": "6",
    },
  });
}

async function proxy(request, env) {
  const inUrl = new URL(request.url);
  const target = new URL(request.url);
  target.protocol = "https:";
  target.host = spaceHost(env);
  target.port = "";

  // Preserve method, body, headers; let CF handle WebSocket upgrades.
  const upstream = new Request(target.toString(), request);
  // hf.space uses Host-based routing internally; setting it explicitly avoids redirects
  upstream.headers.set("host", spaceHost(env));
  // Optional: forward original client info
  upstream.headers.set("x-forwarded-host", inUrl.host);
  upstream.headers.set("x-forwarded-proto", inUrl.protocol.replace(":", ""));

  return fetch(upstream);
}

export default {
  async fetch(request, env, ctx) {
    if (!env.HF_USERNAME || !env.HF_SPACE) {
      return new Response(
        "Worker misconfigured: set HF_USERNAME and HF_SPACE vars.",
        { status: 500 },
      );
    }

    const gated = gateRequest(request, env);
    if (gated) return gated;

    const url = new URL(request.url);

    // Lightweight status endpoint for monitoring / uptime pings
    if (url.pathname === "/__wake/status") {
      const s = await getStage(env);
      return new Response(JSON.stringify(s), {
        headers: { "content-type": "application/json" },
      });
    }

    const { stage } = await getStage(env);

    if (RUNNING_STAGES.has(stage)) {
      // Best path: just proxy. If the Space is mid-startup it will return its
      // own loading screen, which is fine.
      return proxy(request, env);
    }

    if (WAKE_STAGES.has(stage) || stage === "UNKNOWN") {
      // Fire-and-forget the wake call; don't block the response on it.
      ctx.waitUntil(requestWake(env));
      return warmingPage(env, stage, 0);
    }

    // BUILDING / APP_STARTING / etc — don't restart, just show warmup.
    return warmingPage(env, stage, 0);
  },
};
