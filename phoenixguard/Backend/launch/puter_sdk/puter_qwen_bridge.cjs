#!/usr/bin/env node
"use strict";
/*
 * PhoenixGuard Puter Qwen bridge (Node side).
 *
 * Puter's free tier (user-pays model) is served through the Puter.js SDK over a
 * WebSocket; the raw OpenAI-compatible HTTP endpoint requires a paid
 * subscription.  This bridge exists because the Python daemon cannot speak the
 * SDK protocol, so it shells out to `node puter_qwen_bridge.cjs`, which:
 *
 *   1. reads one JSON request object from stdin
 *      { model, max_tokens, temperature, token, content: [ {type, ...}, ... ] }
 *      - content items mirror the OpenAI multimodal shape: {type:"text",
 *        text} and {type:"image_url", image_url:{url:"data:...;base64,..."}}.
 *   2. calls puter.ai.chat(messages, { model, max_tokens, temperature })
 *   3. prints a JSON result on stdout
 *      { ok:true, content, reasoning, usage }  OR  { ok:false, error }
 *
 * Environment: PUTER_TOKEN or the `token` field.  Node 22's bundled undici
 * WebSocket overflows the stack in the SDK, so we install the `ws` package as
 * the global WebSocket first.
 */

global.WebSocket = require("ws");

const { init } = require("@heyputer/puter.js/src/init.cjs");

function read_stdin() {
  return new Promise((resolve) => {
    const chunks = [];
    process.stdin.on("data", (chunk) => chunks.push(chunk));
    process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
  });
}

function main() {
  read_stdin()
    .then((raw) => {
      let request;
      try {
        request = JSON.parse(raw || "{}");
      } catch (error) {
        console.log(JSON.stringify({ ok: false, error: `bad request json: ${error}` }));
        process.exit(0);
        return;
      }
      const token = String(request.token || process.env.PUTER_TOKEN || "").trim();
      if (!token) {
        console.log(JSON.stringify({ ok: false, error: "no puter token configured" }));
        process.exit(0);
        return;
      }
      const model = String(request.model || "qwen/qwen3-vl-30b-a3b");
      const max_tokens = Number(request.max_tokens || 3000);
      const temperature = Number(request.temperature != null ? request.temperature : 0.2);
      const content = Array.isArray(request.content) ? request.content : [];
      const puter = init(token);
      const messages = [{ role: "user", content }];
      puter.ai
        .chat(messages, { model, max_tokens, temperature })
        .then((result) => {
          const message = (result && result.message) || {};
          const usage = (result && result.usage) || null;
          console.log(
            JSON.stringify({
              ok: true,
              content: message.content != null ? String(message.content) : "",
              reasoning: message.reasoning_content != null ? String(message.reasoning_content) : "",
              usage: usage || null,
            })
          );
          process.exit(0);
        })
        .catch((error) => {
          const message = (error && error.message) || (error && error.toString && error.toString()) || String(error);
          console.log(JSON.stringify({ ok: false, error: String(message).slice(0, 2000) }));
          process.exit(0);
        });
    })
    .catch((error) => {
      console.log(JSON.stringify({ ok: false, error: `stdin read failed: ${error}` }));
      process.exit(0);
    });
}

main();
