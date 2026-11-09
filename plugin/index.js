import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

function channelId() {
  if (/^\d{16,22}$/.test(process.env.CVPILOT_CHANNEL_ID || "")) return process.env.CVPILOT_CHANNEL_ID;
  try {
    // Select this non-secret routing field only; never log or retain env data.
    return readFileSync(join(homedir(),".config/cvpilot/controller.env"),"utf8")
      .match(/^CVPILOT_CHANNEL_ID=["']?(\d{16,22})["']?\s*$/m)?.[1] || "";
  } catch { return ""; }
}
const publicChannel=channelId();

// The Discord adapter owns chat and commands. Claim only resolved per-user
// CVPilot Discord sessions so OpenClaw cannot generate a second, disconnected
// reply. Job/CLI model runs and other OpenClaw agents are untouched.
export function claim(event, ctx) {
  if (String(event?.channel || ctx?.channelId || "").toLowerCase() !== "discord") return;
  const uid=String(ctx?.senderId || event?.senderId || "").replace(/^discord:/i, "");
  if (!/^\d{16,22}$/.test(uid)) return;
  const session=String(ctx?.sessionKey || event?.sessionKey || "");
  if (session.includes(":cvpilot:job:")) return;
  const target=String(ctx?.conversationId || "").replace(/^channel:/i, "");
  const dedicated=publicChannel && target===publicChannel;
  const direct=event?.isGroup===false;
  if (!session.startsWith(`agent:career-${uid}:`) && !dedicated && !direct) return;
  return {handled:true};
}

export function guardTool(event,ctx) {
  const key=String(ctx?.sessionKey || "");
  if (!/^agent:career-\d{16,22}:cvpilot:job:/.test(key)) return;
  if (!/:job:(?:chat-|compact-)/.test(key) && !/:source-\d+$/.test(key)) return;
  return {block:true,blockReason:"This CVPilot phase returns text JSON only. File operations require a validated controller action; do not call tools."};
}

export default definePluginEntry({
  id:"cvpilot", name:"CVPilot", description:"Shared Discord conversation routing",
  register(api) { api.on("before_dispatch",claim); api.on("before_tool_call",guardTool); }
});
