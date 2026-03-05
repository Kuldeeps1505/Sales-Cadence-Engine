"use client";
import React, { useState, useEffect, useRef, useCallback } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type Lead = { id: string; name: string; company?: string; email: string; phone: string; language: string; is_dnc: boolean; created_at: string; };
type Campaign = { id: string; name: string; status: string; cadence_config: { max_call_retries: number; retry_delay_seconds: number }; created_at: string; };
type AuditLog = { id: string; lead_id: string; attempt_type: string; status: string; metadata_?: Record<string, any>; attempted_at: string; };
type WorkflowLead = { campaign_lead_id: string; lead_id: string; lead_name: string; lead_email: string; current_state: string; attempt_count: number; is_active: boolean; workflow_task_id?: string; };
type Report = { calls: { total: number; answered: number; no_answer: number; skipped_dnc: number; failed: number }; emails: { total: number; sent: number; failed: number }; leads: { total: number; completed: number; active: number; blocked: number }; };

function fmtLog(log: AuditLog, leads: Lead[]): string {
  const lead = leads.find(l => l.id === log.lead_id);
  const who = lead ? lead.name : "Unknown";
  const t = new Date(log.attempted_at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const m = log.metadata_ || {};
  if (log.attempt_type === "call") {
    if (log.status === "success")       return `[${t}]  📞  ${who} — answered (attempt #${m.attempt ?? "?"})`;
    if (log.status === "no_answer")     return `[${t}]  📞  ${who} — no answer (attempt #${m.attempt ?? "?"})`;
    if (log.status === "skipped_dnc")   return `[${t}]  🚫  ${who} — blocked (DNC list)`;
    if (log.status === "skipped_hours") return `[${t}]  ⏰  ${who} — rescheduled (outside hours)`;
    if (log.status === "failed")        return `[${t}]  ❌  ${who} — call failed`;
  }
  if (log.attempt_type === "email") {
    if (log.status === "success") return `[${t}]  📧  ${who} — email sent to ${m.to ?? lead?.email ?? "?"}  (${m.email_type ?? m.type ?? "followup"})${m.simulated ? " [simulated]" : ""}`;
    if (log.status === "failed")  return `[${t}]  ❌  ${who} — email failed`;
  }
  if (log.attempt_type === "validation") {
    if (log.status === "passed")  return `[${t}]  ✅  ${who} — all validations passed`;
    if (log.status === "blocked") return `[${t}]  🚫  ${who} — blocked: ${(m.failed_checks || []).join(", ")}`;
  }
  if (log.attempt_type === "workflow") return `[${t}]  🏁  ${who} — workflow complete (${m.total_attempts ?? 0} attempts)`;
  return `[${t}]  ℹ️   ${who} — ${log.attempt_type} → ${log.status}`;
}

function logClr(log: AuditLog) {
  if (log.status === "success" || log.status === "passed") return "text-emerald-400";
  if (log.status === "no_answer")                          return "text-amber-400";
  if (log.status.startsWith("skipped") || log.status === "blocked") return "text-gray-500";
  if (log.status === "failed")                             return "text-red-400";
  if (log.status === "completed")                          return "text-blue-400";
  return "text-gray-300";
}

function stateColor(state: string) {
  const m: Record<string, string> = {
    pending: "bg-gray-100 text-gray-600 border-gray-200",
    workflow_started: "bg-blue-50 text-blue-700 border-blue-200",
    validated: "bg-teal-50 text-teal-700 border-teal-200",
    validation_failed: "bg-red-50 text-red-700 border-red-200",
    call_scheduled: "bg-amber-50 text-amber-700 border-amber-200",
    call_in_progress: "bg-orange-50 text-orange-700 border-orange-200",
    call_answered: "bg-emerald-50 text-emerald-700 border-emerald-200",
    call_no_answer: "bg-yellow-50 text-yellow-700 border-yellow-200",
    email_in_progress: "bg-violet-50 text-violet-700 border-violet-200",
    completed: "bg-emerald-100 text-emerald-800 border-emerald-300",
    dnc_blocked: "bg-red-100 text-red-800 border-red-300",
  };
  return m[state] ?? "bg-gray-100 text-gray-500 border-gray-200";
}

function campaignStatusColor(s: string) {
  if (s === "active")    return "text-emerald-700 bg-emerald-50 border-emerald-200";
  if (s === "draft")     return "text-amber-700 bg-amber-50 border-amber-200";
  if (s === "completed") return "text-blue-700 bg-blue-50 border-blue-200";
  return "text-gray-500 bg-gray-50 border-gray-200";
}

function StatCard({ icon, label, value, sub }: { icon: string; label: string; value: string | number; sub?: string }) {
  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 flex flex-col gap-1 hover:border-blue-300 hover:shadow-sm transition-all">
      <div className="flex items-center gap-2 text-gray-400 text-xs font-mono uppercase tracking-widest mb-1"><span>{icon}</span><span>{label}</span></div>
      <div className="text-3xl font-bold text-gray-900 font-mono">{value}</div>
      {sub && <div className="text-xs text-gray-400">{sub}</div>}
    </div>
  );
}

function UploadSection({ onUploaded }: { onUploaded: () => void }) {
  const [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  async function doUpload(file: File) {
    if (!file.name.endsWith(".csv")) { alert("CSV only"); return; }
    setLoading(true); setResult(null);
    const form = new FormData(); form.append("file", file);
    try { const res = await fetch(`${API}/api/v1/leads/upload`, { method: "POST", body: form }); setResult(await res.json()); onUploaded(); }
    catch (e: any) { setResult({ error: e.message }); } finally { setLoading(false); }
  }
  return (
    <div className="bg-white border border-gray-200 rounded-xl p-6 shadow-sm">
      <div className="flex items-center gap-3 mb-4">
        <div className="w-8 h-8 rounded-lg bg-violet-50 border border-violet-200 flex items-center justify-center">📤</div>
        <h2 className="text-gray-900 font-semibold text-lg">Upload Leads</h2>
        <span className="text-xs text-gray-400 font-mono ml-auto">CSV · name, email, phone, company, language, notes</span>
      </div>
      <label className={`relative flex flex-col items-center justify-center border-2 border-dashed rounded-xl p-6 cursor-pointer transition-all ${dragOver ? "border-blue-400 bg-blue-50" : "border-gray-200 hover:border-blue-300 hover:bg-blue-50/30"}`}
        onDragOver={e => { e.preventDefault(); setDragOver(true); }} onDragLeave={() => setDragOver(false)}
        onDrop={e => { e.preventDefault(); setDragOver(false); const f = e.dataTransfer.files[0]; if (f) doUpload(f); }}>
        <input type="file" accept=".csv" className="absolute inset-0 opacity-0 cursor-pointer" onChange={e => { const f = e.target.files?.[0]; if (f) doUpload(f); e.target.value = ""; }} />
        <div className="text-2xl mb-1">{loading ? "⏳" : "📂"}</div>
        <p className="text-gray-600 text-sm">{loading ? "Uploading…" : "Drop CSV or click to browse"}</p>
      </label>
      {result && !result.error && (
        <div className="mt-3 grid grid-cols-3 gap-2">
          {[["✅","Created",result.created,"text-emerald-600","bg-emerald-50 border-emerald-100"],
            ["🔄","Updated",result.updated,"text-amber-600","bg-amber-50 border-amber-100"],
            ["❌","Failed",result.failed,"text-red-500","bg-red-50 border-red-100"]
          ].map(([icon,label,val,color,bg]) => (
            <div key={label as string} className={`rounded-lg p-2.5 text-center border ${bg}`}>
              <div className={`text-lg font-bold font-mono ${color}`}>{val}</div>
              <div className="text-gray-500 text-xs">{icon} {label}</div>
            </div>
          ))}
        </div>
      )}
      {result?.error && <p className="mt-2 text-red-500 text-sm">Error: {result.error}</p>}
    </div>
  );
}

function LeadsSection({ leads, onDNC }: { leads: Lead[]; onDNC: (id: string) => void }) {
  const [search, setSearch] = useState("");
  const filtered = leads.filter(l => `${l.name} ${l.email} ${l.company}`.toLowerCase().includes(search.toLowerCase()));
  return (
    <div className="bg-white border border-gray-200 rounded-xl p-6 shadow-sm">
      <div className="flex items-center gap-3 mb-4">
        <div className="w-8 h-8 rounded-lg bg-blue-50 border border-blue-200 flex items-center justify-center">👥</div>
        <h2 className="text-gray-900 font-semibold text-lg">Leads</h2>
        <span className="ml-auto text-xs font-mono text-gray-400">{leads.length} total</span>
      </div>
      <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search…"
        className="w-full bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-800 placeholder-gray-400 mb-4 focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100" />
      {leads.length === 0 ? <p className="text-gray-400 text-sm text-center py-8">No leads. Upload a CSV.</p> : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="border-b border-gray-100">{["Name","Company","Email","Phone","Language","Status","Action"].map(h => <th key={h} className="text-left text-gray-400 font-mono text-xs uppercase tracking-widest pb-3 pr-4">{h}</th>)}</tr></thead>
            <tbody className="divide-y divide-gray-50">
              {filtered.map(lead => (
                <tr key={lead.id} className="hover:bg-gray-50/80 transition-colors group">
                  <td className="py-2.5 pr-4 text-gray-900 font-medium">{lead.name}</td>
                  <td className="py-2.5 pr-4 text-gray-500">{lead.company || "—"}</td>
                  <td className="py-2.5 pr-4 text-gray-500 font-mono text-xs">{lead.email}</td>
                  <td className="py-2.5 pr-4 text-gray-500 font-mono text-xs">{lead.phone}</td>
                  <td className="py-2.5 pr-4"><span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full border border-gray-200">{lead.language}</span></td>
                  <td className="py-2.5 pr-4">{lead.is_dnc ? <span className="text-xs bg-red-50 text-red-600 border border-red-200 px-2 py-0.5 rounded-full">DNC</span> : <span className="text-xs bg-emerald-50 text-emerald-700 border border-emerald-200 px-2 py-0.5 rounded-full">Active</span>}</td>
                  <td className="py-2.5">{!lead.is_dnc && <button onClick={() => { if (confirm(`Mark ${lead.name} as DNC?`)) onDNC(lead.id); }} className="text-xs text-red-500 opacity-0 group-hover:opacity-100 transition-all border border-red-200 px-2 py-0.5 rounded hover:bg-red-50">Mark DNC</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function WorkflowTracker({ campaignId, leads }: { campaignId: string; leads: Lead[] }) {
  const [wfLeads, setWfLeads] = useState<WorkflowLead[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const ref = useRef<NodeJS.Timeout | null>(null);
  useEffect(() => {
    if (!campaignId) return;
    const poll = async () => {
      try {
        const [wRes, rRes] = await Promise.all([fetch(`${API}/api/v1/campaigns/${campaignId}/workflow-status`), fetch(`${API}/api/v1/campaigns/${campaignId}/report`)]);
        if (wRes.ok) setWfLeads(await wRes.json());
        if (rRes.ok) setReport(await rRes.json());
      } catch {}
    };
    poll(); ref.current = setInterval(poll, 3000);
    return () => { if (ref.current) clearInterval(ref.current); };
  }, [campaignId]);
  const allDone = wfLeads.length > 0 && wfLeads.every(l => !l.is_active);
  return (
    <div className="space-y-4">
       {/* Live Lead States */}
      {wfLeads.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-xl p-5 shadow-sm">
          <div className="flex items-center gap-3 mb-4">
            <span className="text-lg">📊</span>
            <h3 className="font-semibold text-gray-900">Live Lead States</h3>
            <span className={`ml-auto flex items-center gap-1.5 text-xs font-medium ${allDone ? "text-emerald-600" : "text-blue-600"}`}>
              <span className={`w-1.5 h-1.5 rounded-full inline-block ${allDone ? "bg-emerald-500" : "bg-blue-500 animate-pulse"}`}></span>
              {allDone ? "All complete" : "Running…"}
            </span>
          </div>
          <div className="space-y-2 max-h-56 overflow-y-auto">
            {wfLeads.map(wl => (
              <div key={wl.campaign_lead_id} className="flex items-center gap-3 p-3 bg-gray-50 rounded-lg border border-gray-100">
                <div className="flex-1 min-w-0">
                  <p className="text-gray-800 text-sm font-medium">{wl.lead_name}</p>
                  <p className="text-gray-400 text-xs font-mono">{wl.lead_email}</p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-xs text-gray-400 font-mono">{wl.attempt_count} attempts</span>
                  <span className={`text-xs px-2 py-0.5 rounded-full border font-mono ${stateColor(wl.current_state)}`}>{wl.current_state.replace(/_/g, " ")}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {report && (
        <div className="bg-white border border-gray-200 rounded-xl p-5 shadow-sm">
          <div className="flex items-center gap-2 mb-4"><span className="text-lg">📈</span><h3 className="font-semibold text-gray-900">Campaign Report</h3><span className="text-xs text-gray-400 font-mono ml-auto">auto-refreshing</span></div>
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-blue-50 border border-blue-100 rounded-xl p-4">
              <p className="text-blue-700 font-semibold text-sm mb-3">📞 Calls</p>
              <div className="space-y-1.5 text-xs font-mono">
                {[["Total",report.calls.total,"text-gray-800"],["Answered",report.calls.answered,"text-emerald-700"],["No Answer",report.calls.no_answer,"text-amber-700"],["Failed",report.calls.failed,"text-red-600"],["DNC Skip",report.calls.skipped_dnc,"text-gray-500"]].map(([l,v,c]) => (
                  <div key={l as string} className="flex justify-between"><span className="text-gray-500">{l}</span><span className={`font-bold ${c}`}>{v}</span></div>
                ))}
              </div>
            </div>
            <div className="bg-violet-50 border border-violet-100 rounded-xl p-4">
              <p className="text-violet-700 font-semibold text-sm mb-3">📧 Emails</p>
              <div className="space-y-1.5 text-xs font-mono">
                {[["Total",report.emails.total,"text-gray-800"],["Sent",report.emails.sent,"text-emerald-700"],["Failed",report.emails.failed,"text-red-600"],["Rate",`${report.emails.total>0?Math.round(report.emails.sent/report.emails.total*100):0}%`,"text-violet-700"]].map(([l,v,c]) => (
                  <div key={l as string} className="flex justify-between"><span className="text-gray-500">{l}</span><span className={`font-bold ${c}`}>{v}</span></div>
                ))}
              </div>
            </div>
            <div className="bg-emerald-50 border border-emerald-100 rounded-xl p-4">
              <p className="text-emerald-700 font-semibold text-sm mb-3">👥 Leads</p>
              <div className="space-y-1.5 text-xs font-mono">
                {[["Total",report.leads.total,"text-gray-800"],["Completed",report.leads.completed,"text-emerald-700"],["Active",report.leads.active,"text-blue-700"],["Blocked",report.leads.blocked,"text-red-600"],["Rate",`${report.leads.total>0?Math.round(report.leads.completed/report.leads.total*100):0}%`,"text-emerald-700"]].map(([l,v,c]) => (
                  <div key={l as string} className="flex justify-between"><span className="text-gray-500">{l}</span><span className={`font-bold ${c}`}>{v}</span></div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function CampaignSection({ leads, campaigns, onCreated }: { leads: Lead[]; campaigns: Campaign[]; onCreated: () => void }) {
  const [selected, setSelected] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [retries, setRetries] = useState(3);
  const [delay, setDelay] = useState(15);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [activeCampaign, setActiveCampaign] = useState<Campaign | null>(null);
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [showWorkflow, setShowWorkflow] = useState(false);
  const intervalRef = useRef<NodeJS.Timeout | null>(null);
  useEffect(() => () => { if (intervalRef.current) clearInterval(intervalRef.current); }, []);

  const startLogPoll = (campaignId: string) => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    intervalRef.current = setInterval(async () => { try { const r = await fetch(`${API}/api/v1/campaigns/${campaignId}/logs`); if (r.ok) setLogs(await r.json()); } catch {} }, 3000);
    setTimeout(() => { if (intervalRef.current) { clearInterval(intervalRef.current); intervalRef.current = null; } }, 300000);
  };

  async function createAndStart(useWorkflow: boolean) {
    if (busy || !name || selected.length === 0) return;
    setBusy(true); setMsg(null); setLogs([]);
    try {
      const res = await fetch(`${API}/api/v1/campaigns/`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, lead_ids: selected, max_call_retries: retries, retry_delay_seconds: delay }) });
      if (!res.ok) throw new Error(await res.text());
      const campaign: Campaign = await res.json();
      const endpoint = useWorkflow ? `${API}/api/v1/campaigns/${campaign.id}/run-workflow` : `${API}/api/v1/campaigns/${campaign.id}/start`;
      await fetch(endpoint, { method: "POST" });
      setActiveCampaign(campaign); setShowWorkflow(useWorkflow);
      setMsg(`"${campaign.name}" started with ${useWorkflow ? "full orchestration workflow" : "basic cadence"}`);
      onCreated(); setName(""); setSelected([]);
      startLogPoll(campaign.id);
    } catch (e: any) { setMsg(`Error: ${e.message}`); } finally { setBusy(false); }
  }

  async function viewCampaign(c: Campaign) {
    setActiveCampaign(c); setShowWorkflow(true);
    try { const r = await fetch(`${API}/api/v1/campaigns/${c.id}/logs`); if (r.ok) setLogs(await r.json()); } catch {}
    startLogPoll(c.id);
  }

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <div className="bg-white border border-gray-200 rounded-xl p-6 shadow-sm">
          <div className="flex items-center gap-3 mb-5">
            <div className="w-8 h-8 rounded-lg bg-blue-50 border border-blue-200 flex items-center justify-center">🚀</div>
            <h2 className="text-gray-900 font-semibold text-lg">New Campaign</h2>
          </div>
          <div className="space-y-4">
            <div>
              <label className="text-xs text-gray-400 font-mono uppercase tracking-wider block mb-1.5">Campaign Name</label>
              <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. AI Outreach Q1" className="w-full bg-gray-50 border border-gray-200 rounded-lg px-3 py-2.5 text-sm text-gray-900 placeholder-gray-400 focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100 transition-all" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div><label className="text-xs text-gray-400 font-mono uppercase tracking-wider block mb-1.5">Max Call Retries</label><input type="number" min={1} max={10} value={retries} onChange={e => setRetries(+e.target.value)} className="w-full bg-gray-50 border border-gray-200 rounded-lg px-3 py-2.5 text-sm text-gray-900 focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100" /></div>
              <div><label className="text-xs text-gray-400 font-mono uppercase tracking-wider block mb-1.5">Retry Delay (s)</label><input type="number" min={5} max={3600} value={delay} onChange={e => setDelay(+e.target.value)} className="w-full bg-gray-50 border border-gray-200 rounded-lg px-3 py-2.5 text-sm text-gray-900 focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100" /></div>
            </div>
            <div>
              <div className="flex justify-between mb-1.5"><label className="text-xs text-gray-400 font-mono uppercase tracking-wider">Select Leads</label><span className="text-xs text-gray-400">{selected.length} selected</span></div>
              <div className="max-h-40 overflow-y-auto bg-gray-50 border border-gray-200 rounded-lg divide-y divide-gray-100">
                {leads.length === 0 ? <p className="text-gray-400 text-xs text-center py-5">Upload leads first</p> : leads.map(l => (
                  <label key={l.id} className={`flex items-center gap-3 px-3 py-2 cursor-pointer transition-colors ${selected.includes(l.id) ? "bg-blue-50" : "hover:bg-gray-100"}`}>
                    <input type="checkbox" checked={selected.includes(l.id)} onChange={() => setSelected(prev => prev.includes(l.id) ? prev.filter(x => x !== l.id) : [...prev, l.id])} className="accent-blue-600 w-4 h-4" />
                    <div className="flex-1 min-w-0"><p className="text-gray-800 text-sm font-medium truncate">{l.name}</p><p className="text-gray-400 text-xs truncate">{l.email}</p></div>
                    {l.is_dnc && <span className="text-xs bg-red-50 text-red-600 px-1.5 py-0.5 rounded border border-red-200 shrink-0">DNC</span>}
                  </label>
                ))}
              </div>
            </div>
            <div className="grid grid-cols-1 gap-2">
              <button onClick={() => createAndStart(true)} disabled={!name || selected.length === 0 || busy} className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-40 text-white font-semibold py-2.5 rounded-lg transition-colors text-sm flex items-center justify-center gap-2 shadow-sm">
                {busy ? "⏳ Starting…" : "🔀 Run Full Workflow (chain + group + chord)"}
              </button>
              <button onClick={() => createAndStart(false)} disabled={!name || selected.length === 0 || busy} className="w-full bg-gray-100 hover:bg-gray-200 disabled:opacity-40 text-gray-700 font-semibold py-2 rounded-lg transition-colors text-sm flex items-center justify-center gap-2 border border-gray-200">
                {busy ? "⏳" : "▶ Basic Cadence (Stage 1)"}
              </button>
            </div>
            {msg && <p className={`text-sm text-center font-medium ${msg.startsWith("Error") ? "text-red-500" : "text-emerald-600"}`}>{msg}</p>}
          </div>
        </div>

        <div className="space-y-4">
          <div className="bg-white border border-gray-200 rounded-xl p-5 shadow-sm">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-8 h-8 rounded-lg bg-amber-50 border border-amber-200 flex items-center justify-center">📋</div>
              <h2 className="text-gray-900 font-semibold">Campaigns</h2>
              <span className="ml-auto text-xs font-mono text-gray-400">{campaigns.length} total</span>
            </div>
            <div className="space-y-1.5 max-h-48 overflow-y-auto">
              {campaigns.length === 0 ? <p className="text-gray-400 text-xs text-center py-5">No campaigns yet</p> : campaigns.map(c => (
                <div key={c.id} className={`flex items-center gap-3 p-2.5 rounded-lg border cursor-pointer transition-all ${activeCampaign?.id === c.id ? "bg-blue-50 border-blue-200" : "border-gray-100 hover:bg-gray-50"}`} onClick={() => viewCampaign(c)}>
                  <div className="flex-1 min-w-0"><p className="text-gray-800 text-sm font-medium truncate">{c.name}</p><p className="text-gray-400 text-xs font-mono">{new Date(c.created_at).toLocaleDateString("en-IN")}</p></div>
                  <span className={`text-xs px-2 py-0.5 rounded-full border font-mono shrink-0 ${campaignStatusColor(c.status)}`}>{c.status}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="bg-white border border-gray-200 rounded-xl p-5 shadow-sm">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-8 h-8 rounded-lg bg-rose-50 border border-rose-200 flex items-center justify-center">📜</div>
              <h2 className="text-gray-900 font-semibold">Audit Log</h2>
              {activeCampaign && <span className="text-xs text-gray-400 truncate max-w-[100px]">{activeCampaign.name}</span>}
              {logs.length > 0 && <span className="ml-auto flex items-center gap-1.5 text-xs text-emerald-600 font-medium"><span className="w-1.5 h-1.5 bg-emerald-500 rounded-full animate-pulse inline-block"></span>Live</span>}
            </div>
            <div className="bg-gray-950 rounded-lg border border-gray-800 h-48 overflow-y-auto p-3 font-mono text-xs">
              {logs.length === 0 ? <p className="text-gray-600 text-center mt-6">{activeCampaign ? "Waiting for activity…" : "Click a campaign"}</p>
                : [...logs].reverse().map(log => <div key={log.id} className={`py-0.5 leading-relaxed ${logClr(log)}`}>{fmtLog(log, leads)}</div>)}
            </div>
            {logs.length > 0 && (
              <div className="mt-2.5 grid grid-cols-4 gap-1.5">
                {[["Calls",logs.filter(l=>l.attempt_type==="call").length,"text-blue-600","bg-blue-50 border-blue-100"],
                  ["Answered",logs.filter(l=>l.status==="success"&&l.attempt_type==="call").length,"text-emerald-600","bg-emerald-50 border-emerald-100"],
                  ["Emails",logs.filter(l=>l.attempt_type==="email").length,"text-violet-600","bg-violet-50 border-violet-100"],
                  ["Validated",logs.filter(l=>l.attempt_type==="validation").length,"text-teal-600","bg-teal-50 border-teal-100"],
                ].map(([label,val,color,bg]) => (
                  <div key={label as string} className={`rounded p-2 border text-center ${bg}`}>
                    <div className={`text-base font-bold font-mono ${color}`}>{val}</div>
                    <div className="text-gray-400 text-xs">{label}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {activeCampaign && showWorkflow && <WorkflowTracker campaignId={activeCampaign.id} leads={leads} />}
    </div>
  );
}

export default function Dashboard() {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [tab, setTab] = useState<"overview"|"leads"|"campaign">("overview");
  const fetchLeads = useCallback(() => fetch(`${API}/api/v1/leads/`).then(r=>r.json()).then(setLeads).catch(()=>{}), []);
  const fetchCampaigns = useCallback(() => fetch(`${API}/api/v1/campaigns/`).then(r=>r.json()).then(setCampaigns).catch(()=>{}), []);
  useEffect(() => { fetchLeads(); fetchCampaigns(); }, [fetchLeads, fetchCampaigns]);
  async function handleDNC(id: string) { await fetch(`${API}/api/v1/leads/${id}/dnc`, { method: "POST" }); fetchLeads(); }
  const activeCampaigns = campaigns.filter(c => c.status === "active").length;
  const dncCount = leads.filter(l => l.is_dnc).length;
  const TABS = [{ key: "overview", label: "Overview", icon: "⬡" }, { key: "leads", label: "Leads", icon: "👥" }, { key: "campaign", label: "Campaigns", icon: "🚀" }] as const;
  return (
    <div className="min-h-screen bg-gray-50 text-gray-900" style={{ fontFamily: "'IBM Plex Mono', 'Courier New', monospace" }}>
      <header className="border-b border-gray-200 bg-white sticky top-0 z-10 shadow-sm">
        <div className="max-w-7xl mx-auto px-6 h-14 flex items-center gap-4">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-md bg-blue-600 flex items-center justify-center text-white font-bold text-sm shadow-sm">S</div>
            <span className="text-gray-900 font-semibold tracking-tight">Sales Cadence Engine</span>
            <span className="text-gray-400 text-xs border border-gray-200 rounded px-1.5 py-0.5 bg-gray-50 ml-1">v2.0 · Stage 2</span>
          </div>
          <nav className="flex items-center gap-1 ml-8">
            {TABS.map(t => (
              <button key={t.key} onClick={() => setTab(t.key)} className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${tab === t.key ? "bg-blue-600 text-white shadow-sm" : "text-gray-500 hover:text-gray-800 hover:bg-gray-100"}`}>
                <span>{t.icon}</span><span>{t.label}</span>
              </button>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <a href="http://localhost:5555" target="_blank" rel="noreferrer" className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-amber-600 transition-all border border-gray-200 hover:border-amber-300 hover:bg-amber-50 px-3 py-1.5 rounded-lg">🌸 Flower</a>
            <div className="flex items-center gap-1.5 text-xs text-emerald-700 font-medium bg-emerald-50 border border-emerald-200 px-3 py-1.5 rounded-lg">
              <span className="w-1.5 h-1.5 bg-emerald-500 rounded-full animate-pulse inline-block"></span>Connected
            </div>
          </div>
        </div>
      </header>
      <main className="max-w-7xl mx-auto px-6 py-8 space-y-8">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <StatCard icon="👥" label="Total Leads" value={leads.length} sub="in database" />
          <StatCard icon="🚀" label="Active Campaigns" value={activeCampaigns} sub="running now" />
          <StatCard icon="📋" label="Total Campaigns" value={campaigns.length} sub="all time" />
          <StatCard icon="🚫" label="DNC Leads" value={dncCount} sub="blocked" />
        </div>
        <div className="bg-white border border-gray-200 rounded-xl px-5 py-3 shadow-sm flex flex-wrap gap-4 text-xs font-mono items-center">
          <span className="text-gray-400">Celery primitives:</span>
          {[["chain()","Sequential — result flows to next task","text-emerald-600 bg-emerald-50 border-emerald-200"],
            ["group()","Parallel — all fire simultaneously","text-blue-600 bg-blue-50 border-blue-200"],
            ["chord()","Parallel → single callback","text-violet-600 bg-violet-50 border-violet-200"],
            ["retry","Exponential backoff on failure","text-amber-600 bg-amber-50 border-amber-200"],
            ["signals","Auto-update TaskRecord DB","text-rose-600 bg-rose-50 border-rose-200"],
          ].map(([p,d,c]) => <span key={p as string} className={`px-2 py-0.5 rounded border ${c}`} title={d as string}>{p}</span>)}
        </div>
        {tab === "overview" && <div className="space-y-6"><UploadSection onUploaded={fetchLeads} /><CampaignSection leads={leads} campaigns={campaigns} onCreated={() => { fetchLeads(); fetchCampaigns(); }} /></div>}
        {tab === "leads" && <div className="space-y-6"><UploadSection onUploaded={fetchLeads} /><LeadsSection leads={leads} onDNC={handleDNC} /></div>}
        {tab === "campaign" && <CampaignSection leads={leads} campaigns={campaigns} onCreated={() => { fetchLeads(); fetchCampaigns(); }} />}
      </main>
      <footer className="border-t border-gray-200 bg-white mt-16 py-4 text-center text-gray-400 text-xs">
        Sales Cadence Engine v2.0 · chain + group + chord + result backend · FastAPI + Celery + Redis + PostgreSQL
      </footer>
    </div>
  );
}





