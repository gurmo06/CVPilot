import { createConnection } from "node:net";
import { homedir } from "node:os";
import { join } from "node:path";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

const socketPath = process.env.CVPILOT_SOCKET || join(homedir(), ".local", "state", "cvpilot", "controller.sock");

function uidFrom(value) {
  if (typeof value !== "string") return null;
  const valueClean = value.replace(/^discord:/i, "");
  return /^\d{16,22}$/.test(valueClean) ? valueClean : null;
}
function send(req) {
  return new Promise((resolve, reject) => {
    const sock = createConnection(socketPath);
    let bytes = "";
    const timer = setTimeout(() => { sock.destroy(); reject(new Error("CVPilot controller timed out")); }, 10000);
    sock.setEncoding("utf8");
    sock.on("connect", () => sock.write(JSON.stringify(req) + "\n"));
    sock.on("data", (data) => {
      bytes += data;
      if (bytes.length > 65536) { sock.destroy(); reject(new Error("Controller response too large")); }
      if (bytes.includes("\n")) {
        clearTimeout(timer); sock.end();
        try { resolve(JSON.parse(bytes.slice(0, bytes.indexOf("\n")))); }
        catch (e) { reject(e); }
      }
    });
    sock.on("error", (e) => { clearTimeout(timer); reject(e); });
    sock.on("close", () => clearTimeout(timer));
  });
}
function parse(body) {
  const input=body.trim();
  const [word, ...rest]=input.split(/\s+/);
  const action=(word || "help").toLowerCase();
  const tail=rest.join(" ").trim();
  if (action === "init") return {action,args:{github_username:tail}};
  if (action === "tailor") return {action,args:{url:tail}};
  if (action === "repo" || action === "status") return {action,args:{}};
  if (action === "settings") {
    const [option="",value="show"]=tail.split(/\s+/);
    return {action,args:{option,value}};
  }
  if (action === "master") {
    const [operation="",role="",...words]=tail.split(/\s+/);
    return {action,args:{operation,role,instructions:words.join(" ")}};
  }
  if (action === "profile") {
    const [operation="",...words]=tail.split(/\s+/);
    return {action,args:{operation,instructions:words.join(" ")}};
  }
  return {action:"help",args:{}};
}
const help = [
  "**CVPilot commands** (use in a DM):",
  "`/cvpilot init GITHUB_USERNAME` — create your private CVPilot/career-<Discord ID> repo",
  "`/cvpilot repo` — show your GitHub repo link",
  "`/cvpilot tailor https://...` — queue a job-specific one-page resume",
  "`/cvpilot master get swe` — link to your master resume",
  "`/cvpilot master edit swe <specific instructions>` — edit on review branch (opt-in required)",
  "`/cvpilot master upload swe` — shows current manual upload instructions",
  "`/cvpilot profile show|sync|edit <facts>` — manage master profile",
  "`/cvpilot settings master_edit on|off|show` — master edit permission",
  "`/cvpilot status` — job status",
].join("\n");

export default definePluginEntry({
  id:"cvpilot",name:"CVPilot",description:"Career repository and FIFO job controller",
  register(api) {
    // Slash commands are owned by the CVPilot v3 Discord service.

    // Natural-language auto-queue for clear tailoring requests. Verify this
    // hook's sender attribution in your specific OpenClaw build before use.
    // Disabled unless the operator explicitly enables it; no model can grant
    // its own identity or mutate settings via this hook.
    if(process.env.CVPILOT_NATURAL_LANGUAGE === "1") {
      api.on("before_agent_reply", async (event, ctx) => {
        const uid=uidFrom(ctx?.senderId);
        const channel=String(ctx?.channelId || ctx?.channel || ctx?.messageProvider || "").toLowerCase();
        if(!uid || !channel.includes("discord")) return;
        const body=String(event?.cleanedBody || "").trim();
        if(body.startsWith("/")) return;
        const hasTailorIntent=/\b(tailor|customiz(?:e|ing)|make (?:me )?a resume|create (?:me )?a resume)\b/i.test(body);
        const url=body.match(/https:\/\/[^\s<>]+/i)?.[0];
        if(!hasTailorIntent || !url) return;
        try {
          const res=await send({sender_id:uid,action:"tailor",args:{url}});
          return {handled:true,reply:{text:res.text}};
        } catch(e) {
          return {handled:true,reply:{text:`CVPilot: request could not be queued: ${String(e)}`}};
        }
      },{eligibleTriggers:["user"]});
    }
  }
});
