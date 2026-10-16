import { useCallback, useEffect, useMemo, useState } from "react";

type Project = { id: string; name: string; enabled: boolean; rate_limit_rpm: number; monthly_budget_usd: number | null; pii_policy: string };
type Usage = { admitted_requests: number; completed_requests: number; input_tokens: number; output_tokens: number; spend_usd: number; unknown_cost_requests: number; monthly_budget_usd: number | null; budget_remaining_usd: number | null };
type Health = { model_id: string; provider: string; circuit_state: string; health_score: number; total_successes: number; total_failures: number; failure_ewma: number; latency_ewma_ms: number | null };
type Policy = { id: string; name: string; version: number; policy: string; status: string; canary_percent: number; rollback_reason: string | null };
type Inference = { receipt_id: string; created_at: string; selected_model: string; selected_provider: string; policy: string; actual_input_tokens: number | null; actual_output_tokens: number | null; actual_cost_usd: number | null; provider_latency_ms: number | null; rollout_group: string | null; pii_detected_types: string[] };
type Audit = { id: string; sequence: number; event_type: string; actor_type: string; occurred_at: string; event_hash: string };
type Integrity = { valid: boolean; event_count: number; first_invalid_sequence: number | null };

const initialBase = sessionStorage.getItem("ii-api-base") || (location.port === "5173" ? "http://127.0.0.1:8000" : location.origin);

export default function App() {
  const [apiBase, setApiBase] = useState(initialBase);
  const [adminKey, setAdminKey] = useState(sessionStorage.getItem("ii-admin-key") || "");
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [usage, setUsage] = useState<Usage | null>(null);
  const [health, setHealth] = useState<Health[]>([]);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [inferences, setInferences] = useState<Inference[]>([]);
  const [audits, setAudits] = useState<Audit[]>([]);
  const [integrity, setIntegrity] = useState<Integrity | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [connected, setConnected] = useState(false);

  const call = useCallback(async <T,>(path: string): Promise<T> => {
    const response = await fetch(`${apiBase.replace(/\/$/, "")}${path}`, {
      headers: { "X-InferenceIQ-Admin-Key": adminKey },
    });
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
    return response.json() as Promise<T>;
  }, [adminKey, apiBase]);

  const loadGlobal = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [projectData, healthData, policyData] = await Promise.all([
        call<Project[]>("/v1/projects"),
        call<Health[]>("/v1/provider-health"),
        call<Policy[]>("/v1/policy-workflows"),
      ]);
      setProjects(projectData);
      setHealth(healthData);
      setPolicies(policyData);
      setProjectId((current) => current || projectData[0]?.id || "");
      setConnected(true);
      sessionStorage.setItem("ii-api-base", apiBase);
      sessionStorage.setItem("ii-admin-key", adminKey);
    } catch (cause) {
      setConnected(false);
      setError(cause instanceof Error ? cause.message : "Unable to connect");
    } finally {
      setLoading(false);
    }
  }, [adminKey, apiBase, call]);

  const loadProject = useCallback(async () => {
    if (!projectId || !connected) return;
    try {
      const [usageData, inferenceData, auditData, integrityData] = await Promise.all([
        call<Usage>(`/v1/projects/${projectId}/usage`),
        call<Inference[]>(`/v1/projects/${projectId}/inferences?limit=12`),
        call<Audit[]>(`/v1/projects/${projectId}/audit-events?limit=8`),
        call<Integrity>(`/v1/projects/${projectId}/audit-integrity`),
      ]);
      setUsage(usageData);
      setInferences(inferenceData);
      setAudits(auditData.slice(-8).reverse());
      setIntegrity(integrityData);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to load project");
    }
  }, [call, connected, projectId]);

  useEffect(() => { if (adminKey) void loadGlobal(); }, []);
  useEffect(() => { void loadProject(); }, [loadProject]);
  useEffect(() => {
    if (!connected) return;
    const timer = window.setInterval(() => { void loadGlobal(); void loadProject(); }, 15000);
    return () => window.clearInterval(timer);
  }, [connected, loadGlobal, loadProject]);

  const selectedProject = projects.find((project) => project.id === projectId);
  const openCircuits = health.filter((item) => item.circuit_state === "open").length;
  const activeCanaries = policies.filter((item) => item.status === "canary").length;
  const averageLatency = useMemo(() => {
    const values = inferences.map((item) => item.provider_latency_ms).filter((value): value is number => value !== null);
    return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
  }, [inferences]);

  if (!connected) {
    return <main className="connect-shell">
      <section className="connect-card">
        <div className="brand-mark">IQ</div>
        <p className="eyebrow">INFERENCE CONTROL PLANE</p>
        <h1>See every model decision.</h1>
        <p className="muted">Connect to your InferenceIQ API to inspect cost, reliability, policy rollouts, and privacy controls.</p>
        <label>API base URL<input value={apiBase} onChange={(event) => setApiBase(event.target.value)} /></label>
        <label>Admin key<input type="password" value={adminKey} onChange={(event) => setAdminKey(event.target.value)} onKeyDown={(event) => event.key === "Enter" && void loadGlobal()} /></label>
        {error && <p className="error">{error}</p>}
        <button onClick={() => void loadGlobal()} disabled={loading || !adminKey}>{loading ? "Connecting…" : "Open control room"}</button>
      </section>
    </main>;
  }

  return <div className="app-shell">
    <aside>
      <div className="logo"><span>IQ</span><strong>InferenceIQ</strong></div>
      <nav><a className="active">Overview</a><a href="#health">Provider health</a><a href="#policies">Policy lab</a><a href="#audit">Audit chain</a></nav>
      <div className="side-foot"><span className="live-dot" /> Live · refreshes every 15s<button className="link-button" onClick={() => { sessionStorage.clear(); setConnected(false); }}>Disconnect</button></div>
    </aside>
    <main className="content">
      <header><div><p className="eyebrow">CONTROL ROOM</p><h1>Inference overview</h1></div><div className="header-actions"><select value={projectId} onChange={(event) => setProjectId(event.target.value)}>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select><button className="refresh" onClick={() => { void loadGlobal(); void loadProject(); }}>Refresh</button></div></header>
      {error && <div className="error-banner">{error}<button onClick={() => setError("")}>×</button></div>}
      <section className="project-strip"><div><span>PROJECT</span><strong>{selectedProject?.name || "No project"}</strong></div><div><span>PRIVACY</span><strong>{selectedProject?.pii_policy || "—"}</strong></div><div><span>RATE LIMIT</span><strong>{selectedProject?.rate_limit_rpm || 0} rpm</strong></div><div><span>STATUS</span><strong className="healthy">● Operational</strong></div></section>
      <section className="stats-grid">
        <Stat label="Monthly spend" value={money(usage?.spend_usd)} note={usage?.monthly_budget_usd == null ? "No budget configured" : `${money(usage.budget_remaining_usd)} remaining`} />
        <Stat label="Requests" value={compact(usage?.completed_requests)} note={`${compact((usage?.input_tokens || 0) + (usage?.output_tokens || 0))} total tokens`} />
        <Stat label="Recent latency" value={averageLatency == null ? "—" : `${averageLatency.toFixed(1)} ms`} note="Average of recent receipts" />
        <Stat label="System risk" value={openCircuits ? `${openCircuits} open` : "Clear"} note={`${activeCanaries} active canary`} accent={openCircuits > 0} />
      </section>
      <section className="two-column">
        <Panel id="health" title="Provider health" kicker="LIVE SIGNALS"><div className="health-list">{health.length ? health.map((item) => <div className="health-row" key={item.model_id}><div><strong>{item.model_id}</strong><span>{item.provider}</span></div><div className="health-meter"><i style={{ width: `${item.health_score * 100}%` }} /></div><div className={`pill ${item.circuit_state}`}>{item.circuit_state}</div><div className="metric"><strong>{item.latency_ewma_ms?.toFixed(0) || "—"}</strong><span>ms EWMA</span></div></div>) : <Empty text="Health appears after the first provider call." />}</div></Panel>
        <Panel id="policies" title="Policy rollouts" kicker="LANGGRAPH"><div className="policy-list">{policies.length ? policies.slice(0, 6).map((policy) => <div className="policy-row" key={policy.id}><div className="policy-icon">{policy.policy[0].toUpperCase()}</div><div><strong>{policy.name} <small>v{policy.version}</small></strong><span>{policy.policy} · {policy.canary_percent}% canary</span></div><div className={`pill ${policy.status}`}>{policy.status.replace("_", " ")}</div></div>) : <Empty text="No policy versions have been proposed." />}</div></Panel>
      </section>
      <Panel title="Recent inference receipts" kicker="DECISION EVIDENCE"><div className="table-wrap"><table><thead><tr><th>Receipt</th><th>Model</th><th>Policy</th><th>Latency</th><th>Tokens</th><th>Cost</th><th>Privacy</th></tr></thead><tbody>{inferences.map((item) => <tr key={item.receipt_id}><td className="mono">{item.receipt_id.slice(0, 14)}…</td><td><strong>{item.selected_model}</strong><small>{item.selected_provider}</small></td><td>{item.policy}{item.rollout_group && <small>{item.rollout_group}</small>}</td><td>{item.provider_latency_ms?.toFixed(1) || "—"} ms</td><td>{compact((item.actual_input_tokens || 0) + (item.actual_output_tokens || 0))}</td><td>{money(item.actual_cost_usd)}</td><td>{item.pii_detected_types.length ? <span className="pill warning">redacted</span> : <span className="pill closed">clear</span>}</td></tr>)}</tbody></table>{!inferences.length && <Empty text="Authenticated inference receipts will appear here." />}</div></Panel>
      <Panel id="audit" title="Signed audit chain" kicker="HMAC-SHA256" action={<span className={`integrity ${integrity?.valid ? "valid" : "invalid"}`}>{integrity?.valid ? "✓ Chain verified" : "! Integrity warning"}</span>}><div className="audit-list">{audits.map((event) => <div className="audit-row" key={event.id}><span className="sequence">#{event.sequence}</span><div><strong>{event.event_type}</strong><small>{new Date(event.occurred_at).toLocaleString()} · {event.actor_type}</small></div><code>{event.event_hash.slice(0, 16)}…</code></div>)}{!audits.length && <Empty text="No audit events yet." />}</div></Panel>
    </main>
  </div>;
}

function Stat({ label, value, note, accent = false }: { label: string; value: string; note: string; accent?: boolean }) { return <article className={`stat ${accent ? "accent" : ""}`}><span>{label}</span><strong>{value}</strong><small>{note}</small></article>; }
function Panel({ title, kicker, children, id, action }: { title: string; kicker: string; children: React.ReactNode; id?: string; action?: React.ReactNode }) { return <section className="panel" id={id}><div className="panel-head"><div><span>{kicker}</span><h2>{title}</h2></div>{action}</div>{children}</section>; }
function Empty({ text }: { text: string }) { return <div className="empty">{text}</div>; }
function money(value: number | null | undefined) { return value == null ? "—" : `$${value < 0.01 && value > 0 ? value.toFixed(5) : value.toFixed(2)}`; }
function compact(value: number | null | undefined) { return Intl.NumberFormat("en", { notation: "compact" }).format(value || 0); }
