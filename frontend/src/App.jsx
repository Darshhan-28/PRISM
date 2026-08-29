import { useEffect, useState, useRef } from 'react'

const API = '/api'

// --- helpers ---
const fmtDate = (iso) => {
  try { const d = new Date(iso); return d.toLocaleString() } catch { return iso }
}
const fmtTime = (iso) => {
  try { return new Date(iso).toLocaleTimeString() } catch { return '' }
}
const shortId = (id) => id ? id.slice(0, 8).toUpperCase() : ''

function StatusBadge({ state }) {
  if (!state) return null
  const map = {
    SUPPORTED: { bg: '#ecfdf5', fg: '#065f46', bd: '#a7f3d0', label: 'Supported' },
    PARTIALLY_SUPPORTED: { bg: '#fffbeb', fg: '#92400e', bd: '#fde68a', label: 'Partially supported' },
    INSUFFICIENT_EVIDENCE: { bg: '#f1f5f9', fg: '#334155', bd: '#cbd5e1', label: 'Insufficient evidence' },
    CONFLICTING_EVIDENCE: { bg: '#fff1f2', fg: '#9f1239', bd: '#fecdd3', label: 'Conflicting evidence' },
  }
  const s = map[state] || { bg: '#f1f5f9', fg: '#334155', bd: '#cbd5e1', label: state }
  return <span style={{ background: s.bg, color: s.fg, border: `1px solid ${s.bd}`, padding: '4px 10px', borderRadius: 999, fontSize: 11, fontWeight: 700, letterSpacing: '.04em', textTransform: 'uppercase' }}>{s.label}</span>
}

function Icon({ name, size = 16 }) {
  const s = { width: size, height: size, display: 'inline-block', verticalAlign: 'middle', flexShrink: 0 }
  const common = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.7, strokeLinecap: 'round', strokeLinejoin: 'round' }
  // minimal inline icons using SVG paths
  const icons = {
    overview: <svg style={s} viewBox="0 0 24 24"><g {...common}><rect x="3" y="3" width="7" height="7" rx="1.2"/><rect x="14" y="3" width="7" height="7" rx="1.2"/><rect x="3" y="14" width="7" height="7" rx="1.2"/><rect x="14" y="14" width="7" height="7" rx="1.2"/></g></svg>,
    investigations: <svg style={s} viewBox="0 0 24 24"><g {...common}><path d="M9 5h6l1 2h4v11H4V7h4l1-2z"/><path d="M9 12h6M9 16h6"/></g></svg>,
    documents: <svg style={s} viewBox="0 0 24 24"><g {...common}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M10 13H8M16 17H8M16 13h-2"/></g></svg>,
    evidence: <svg style={s} viewBox="0 0 24 24"><g {...common}><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-2"/><path d="M9 5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2a2 2 0 0 1-2 2h-2a2 2 0 0 1-2-2V5z"/><path d="M9 13h6M9 17h6"/></g></svg>,
    audit: <svg style={s} viewBox="0 0 24 24"><g {...common}><path d="M12 8v5l3 2"/><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 9 9"/></g></svg>,
    settings: <svg style={s} viewBox="0 0 24 24"><g {...common}><circle cx="12" cy="12" r="3"/><path d="M12 1v3M12 20v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M1 12h3M20 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/></g></svg>,
    search: <svg style={s} viewBox="0 0 24 24"><g {...common}><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></g></svg>,
    upload: <svg style={s} viewBox="0 0 24 24"><g {...common}><path d="M12 16V3"/><path d="M8 7l4-4 4 4"/><path d="M4 14v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/></g></svg>,
    chevron: <svg style={s} viewBox="0 0 24 24"><g {...common}><path d="M9 18l6-6-6-6"/></g></svg>,
    check: <svg style={s} viewBox="0 0 24 24"><g {...common}><path d="M5 13l4 4L19 7"/></g></svg>,
    alert: <svg style={s} viewBox="0 0 24 24"><g {...common}><path d="M12 8v5"/><path d="M12 16h.01"/><path d="M10.3 3.3L2.2 17a2 2 0 0 0 1.7 3h16.2a2 2 0 0 0 1.7-3L13.7 3.3a2 2 0 0 0-3.4 0z"/></g></svg>,
    dot: <span style={{ width: 8, height: 8, borderRadius: 999, background: 'currentColor', display: 'inline-block' }} />,
  }
  return icons[name] || null
}

export default function App() {
  const [view, setView] = useState('overview')
  const [health, setHealth] = useState(null)
  const [docs, setDocs] = useState([])
  const [investigations, setInvestigations] = useState([])
  const [current, setCurrent] = useState(null)
  const [graph, setGraph] = useState(null)
  const [audit, setAudit] = useState([])
  const [contradictions, setContradictions] = useState(null)
  const [objective, setObjective] = useState('Investigate abnormal pressure event in Pump P-204 on 2026-08-15')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [docSearch, setDocSearch] = useState('')
  const [evidenceFilter, setEvidenceFilter] = useState('all')
  const [selectedEvidence, setSelectedEvidence] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const fileRef = useRef(null)

  const fetchHealth = async () => {
    try {
      const r = await fetch(`${API}/health`)
      if (r.ok) setHealth(await r.json())
      const d = await fetch(`${API}/documents`).then(x => x.json()).catch(() => ({ documents: [] }))
      setDocs(d.documents || [])
    } catch {}
  }
  useEffect(() => { fetchHealth() }, [])

  const runInvestigation = async (obj) => {
    const target = obj || objective
    if (!target || target.trim().length < 10) { setError('Enter at least 10 characters to describe the investigation.'); return }
    setLoading(true); setError('')
    try {
      const r = await fetch(`${API}/investigations`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ objective: target }) })
      const j = await r.json()
      if (!r.ok) throw new Error(j.detail || 'Investigation failed')
      setCurrent(j)
      setInvestigations(prev => [j, ...prev].slice(0, 20))
      setView('investigations')
      // fetch graph + audit + contradictions
      try { const g = await fetch(`${API}/investigations/${j.investigation_id}/graph`).then(x => x.json()); setGraph(g) } catch { setGraph(null) }
      try { const a = await fetch(`${API}/investigations/${j.investigation_id}/audit`).then(x => x.json()); setAudit(a.events || []) } catch { setAudit([]) }
      try { const q = await fetch(`${API}/query`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query: target }) }).then(x => x.json()); setContradictions(q.contradictions || null) } catch { setContradictions(null) }
      fetchHealth()
    } catch (e) { setError(String(e.message || e)) }
    finally { setLoading(false) }
  }

  const onUploadFiles = async (files) => {
    if (!files || !files.length) return
    setUploading(true); setError('')
    for (const file of files) {
      const fd = new FormData(); fd.append('file', file)
      try {
        const r = await fetch(`${API}/ingest`, { method: 'POST', body: fd })
        if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || 'Upload failed') }
        await r.json()
      } catch (e) { setError(String(e.message || e)); break }
    }
    setUploading(false); fetchHealth()
  }
  const onFileInput = (e) => { onUploadFiles(e.target.files); e.target.value = '' }

  const filteredDocs = docs.filter(d => !docSearch || d.filename.toLowerCase().includes(docSearch.toLowerCase()))
  const evidenceList = current?.evidence_refs || []
  const filteredEvidence = evidenceList.filter(e => {
    if (evidenceFilter === 'all') return true
    if (evidenceFilter === 'high') return (e.score ?? 0) >= 0.7
    return true
  })

  // render citations as clickable spans
  const renderFindings = (text) => {
    if (!text) return null
    const parts = text.split(/(\[[^\]]+\])/g)
    return parts.map((p, i) => {
      if (/^\[[^\]]+\]$/.test(p)) {
        const name = p.slice(1, -1).split('/').pop()
        const match = evidenceList.find(e => e.filename === p.slice(1,-1) || e.chunk_id === p.slice(1,-1) || p.includes(e.filename))
        return <button key={i} onClick={() => { if (match) { setSelectedEvidence(match); setView('evidence') } }} className="citation" title={match ? `${match.filename} — ${match.chunk_id}` : p}>{p}</button>
      }
      return <span key={i}>{p}</span>
    })
  }

  return (
    <div className="app">
      {/* Top bar */}
      <header className="topbar">
        <div className="topbar-left">
          <div className="brand-mark">S</div>
          <div>
            <div className="brand-name">SIH26117 <span>Sovereign Workbench</span></div>
            <div className="brand-sub">Incident Investigation Workbench</div>
          </div>
        </div>
        <div className="topbar-right">
          <div className="system-status">
            <span className="dot ok" /> Local processing
            <span className="sep" />
            <span className="dot ok" /> Evidence validation active
            <span className="sep" />
            <span className="dot ok" /> Audit logging active
          </div>
          <div className="topbar-context">
            {current ? <><span className="ctx-label">Investigation</span><span className="ctx-id">{shortId(current.investigation_id)}</span><StatusBadge state={current.evidence_state} /></> : <span className="ctx-empty">No investigation selected</span>}
          </div>
        </div>
      </header>

      <div className="shell">
        {/* Sidebar */}
        <nav className="sidebar">
          {[
            ['overview', 'Overview', 'overview'],
            ['investigations', 'Investigations', 'investigations'],
            ['documents', 'Documents', 'documents'],
            ['evidence', 'Evidence', 'evidence'],
            ['audit', 'Audit Trail', 'audit'],
            ['settings', 'Settings', 'settings'],
          ].map(([key, label, icon]) => (
            <button key={key} className={`nav-item ${view === key ? 'active' : ''}`} onClick={() => setView(key)}>
              <Icon name={icon} size={18} /><span>{label}</span>
              {key === 'evidence' && contradictions?.has_contradictions ? <span className="nav-badge warn">{contradictions.contradictions.length}</span> : null}
              {key === 'investigations' && investigations.length ? <span className="nav-badge">{investigations.length}</span> : null}
            </button>
          ))}
          <div className="sidebar-foot">
            <div className="foot-label">Evidence indexed</div>
            <div className="foot-value">{health ? `${health.documents} documents · ${health.chunks} segments` : '—'}</div>
            <div className="foot-meta">Local store · Offline</div>
          </div>
        </nav>

        {/* Main */}
        <main className="main">
          {error && <div className="alert error"><Icon name="alert" /> <span>{error}</span><button className="alert-close" onClick={() => setError('')}>Dismiss</button></div>}

          {/* OVERVIEW */}
          {view === 'overview' && (
            <div className="stack">
              <div className="page-head">
                <h1>Overview</h1>
                <p>Operational status and recent investigation activity.</p>
              </div>

              <div className="kpi-grid">
                <div className="card kpi">
                  <div className="kpi-label">Active investigations</div>
                  <div className="kpi-value">{investigations.length || health?.investigations || 0}</div>
                  <div className="kpi-meta">{current ? `Current: ${shortId(current.investigation_id)} · ${current.evidence_state}` : 'No active investigation'}</div>
                </div>
                <div className="card kpi">
                  <div className="kpi-label">Documents indexed</div>
                  <div className="kpi-value">{health?.documents ?? 0}</div>
                  <div className="kpi-meta">{health?.chunks ?? 0} segments indexed</div>
                </div>
                <div className="card kpi">
                  <div className="kpi-label">Evidence items</div>
                  <div className="kpi-value">{current?.evidence_refs?.length ?? 0}</div>
                  <div className="kpi-meta">{current ? `States: ${current.evidence_state}` : 'Run an investigation to generate evidence'}</div>
                </div>
                <div className="card kpi">
                  <div className="kpi-label">System status</div>
                  <div className="kpi-value" style={{ fontSize: 14, fontWeight: 600, color: '#0f766e' }}>Operational</div>
                  <div className="kpi-meta">Local processing · Evidence validation · Audit</div>
                </div>
              </div>

              <div className="two-col">
                <section className="card">
                  <div className="card-head">
                    <h3>Recent investigations</h3>
                    <button className="btn ghost sm" onClick={() => setView('investigations')}>Open</button>
                  </div>
                  {investigations.length === 0 ? (
                    <div className="empty">
                      <div className="empty-title">No investigations yet</div>
                      <div className="empty-text">Start your first investigation to see findings, evidence and audit trail here.</div>
                      <button className="btn primary" onClick={() => setView('investigations')}>Start an investigation</button>
                    </div>
                  ) : (
                    <div className="list">
                      {investigations.slice(0, 5).map(inv => (
                        <button key={inv.investigation_id} className="list-row" onClick={() => { setCurrent(inv); setView('investigations') }}>
                          <div className="list-main">
                            <div className="list-title">{inv.objective.slice(0, 80)}</div>
                            <div className="list-meta">{fmtDate(inv.created_at)} · {shortId(inv.investigation_id)} · {inv.steps_executed?.length || 0} steps</div>
                          </div>
                          <StatusBadge state={inv.evidence_state} />
                        </button>
                      ))}
                    </div>
                  )}
                </section>

                <section className="card">
                  <div className="card-head"><h3>Evidence alerts</h3></div>
                  {contradictions?.has_contradictions ? (
                    <div className="alert warn">
                      <Icon name="alert" /> <strong>Conflicting evidence detected</strong> — {contradictions.contradictions.length} conflict{contradictions.contradictions.length > 1 ? 's' : ''} requires review.
                    </div>
                  ) : (
                    <div className="empty small">
                      <div className="empty-text">No active conflicts. Contradictions appear here when sources disagree (e.g., valve state).</div>
                    </div>
                  )}
                  <div className="stack-sm" style={{ marginTop: 12 }}>
                    <div className="kv"><span>Documents indexed</span><strong>{docs.length}</strong></div>
                    <div className="kv"><span>Segments</span><strong>{health?.chunks ?? 0}</strong></div>
                    <div className="kv"><span>Investigations</span><strong>{health?.investigations ?? 0}</strong></div>
                  </div>
                </section>
              </div>

              <section className="card">
                <div className="card-head"><h3>Recent documents</h3><button className="btn ghost sm" onClick={() => setView('documents')}>View library</button></div>
                <div className="doc-grid mini">
                  {filteredDocs.slice(0, 6).map(d => (
                    <div key={d.id} className="doc-card mini">
                      <div className="doc-type">{d.file_type}</div>
                      <div className="doc-name" title={d.filename}>{d.filename}</div>
                      <div className="doc-meta">{d.page_count ? `${d.page_count} pages` : 'Indexed'} · {d.sha256 ? d.sha256.slice(0, 8) : ''}</div>
                    </div>
                  ))}
                  {filteredDocs.length === 0 && <div className="empty-text">No documents indexed.</div>}
                </div>
              </section>
            </div>
          )}

          {/* INVESTIGATIONS */}
          {view === 'investigations' && (
            <div className="stack">
              <div className="page-head">
                <h1>Investigations</h1>
                <p>Describe what you need to investigate. The workbench will gather evidence, check for conflicts and build a traceable conclusion.</p>
              </div>

              <section className="card composer">
                <div className="composer-head">
                  <h3>Start an investigation</h3>
                  <span className="hint">Local processing only</span>
                </div>
                <label className="field-label" htmlFor="objective">What would you like to investigate?</label>
                <textarea id="objective" className="textarea" rows={3} value={objective} onChange={e => setObjective(e.target.value)} placeholder="Example: Investigate abnormal pressure event in Pump P-204 on 2026-08-15" />
                <div className="composer-foot">
                  <div className="presets">
                    <span className="presets-label">Presets:</span>
                    {[
                      ['Pressure anomaly', 'Investigate abnormal pressure event in Pump P-204 on 2026-08-15'],
                      ['Equipment maintenance', 'Investigate valve replacement status for Pump P-204'],
                      ['Sensor deviation', 'Investigate vibration anomaly for Pump P-204 on 2026-08-15'],
                      ['Safety review', 'Review SOP compliance for Pump P-204 shutdown procedure'],
                    ].map(([label, q]) => (
                      <button key={label} className="chip" onClick={() => setObjective(q)}>{label}</button>
                    ))}
                  </div>
                  <button className="btn primary lg" onClick={() => runInvestigation()} disabled={loading}>{loading ? 'Running…' : 'Start investigation'}</button>
                </div>
                <div className="composer-meta">
                  <span>{docs.length} sources available</span>
                  <span>·</span>
                  <span>{objective.trim().length} characters</span>
                </div>
              </section>

              {loading && (
                <section className="card">
                  <div className="timeline">
                    {['Planning', 'Searching evidence', 'Analyzing data', 'Checking contradictions', 'Building conclusion'].map((label, i) => (
                      <div key={label} className={`tl-step ${i <= 2 ? 'active' : ''}`}>
                        <div className="tl-dot">{i < 2 ? <Icon name="check" size={12} /> : <span />}</div>
                        <div className="tl-label">{label}</div>
                        {i < 4 && <div className="tl-line" />}
                      </div>
                    ))}
                  </div>
                  <div className="loading-text">Gathering evidence and validating findings…</div>
                </section>
              )}

              {!current && !loading && (
                <div className="card empty">
                  <div className="empty-title">No investigation selected</div>
                  <div className="empty-text">Enter a question above and start an investigation. Results, evidence and audit trail will appear here.</div>
                </div>
              )}

              {current && (
                <>
                  <section className="card result">
                    <div className="result-head">
                      <div>
                        <div className="eyebrow">Investigation {shortId(current.investigation_id)} · {fmtDate(current.created_at)}</div>
                        <h2 className="result-title">{current.objective}</h2>
                        <div className="result-meta">{current.steps_executed?.length || 0} steps · {current.evidence_refs?.length || 0} evidence items · {current.status}</div>
                      </div>
                      <StatusBadge state={current.evidence_state} />
                    </div>

                    {current.evidence_state === 'CONFLICTING_EVIDENCE' && (
                      <div className="alert warn">
                        <Icon name="alert" />
                        <div>
                          <strong>Conflicting evidence</strong>
                          <div>Sources disagree on at least one finding. Review the comparison below before acting.</div>
                        </div>
                      </div>
                    )}
                    {current.evidence_state === 'INSUFFICIENT_EVIDENCE' && (
                      <div className="alert neutral">
                        <Icon name="alert" />
                        <div><strong>Insufficient evidence</strong><div>The available sources do not support a confident conclusion for this question.</div></div>
                      </div>
                    )}

                    <div className="section">
                      <h3>Findings</h3>
                      <div className="findings">{renderFindings(current.summary)}</div>
                    </div>

                    {current.steps_executed?.length > 0 && (
                      <details className="details">
                        <summary>Technical details — execution steps</summary>
                        <div className="steps">
                          {current.steps_executed.map(s => (
                            <div key={s.step_no} className="step">
                              <div className="step-head"><span className="step-no">Step {s.step_no}</span><span className={`step-status ${s.success ? 'ok' : 'fail'}`}>{s.success ? 'Completed' : 'Failed'}</span><span className="step-tool">{s.tool}</span></div>
                              <div className="step-rationale">{s.rationale}</div>
                              {s.error && <div className="step-error">{s.error}</div>}
                            </div>
                          ))}
                        </div>
                      </details>
                    )}
                  </section>

                  {/* Contradiction comparison */}
                  {contradictions?.has_contradictions && (
                    <section className="card">
                      <div className="card-head"><h3>Evidence conflicts</h3><span className="badge warn sm">{contradictions.contradictions.length} conflict{contradictions.contradictions.length > 1 ? 's' : ''}</span></div>
                      <div className="conflicts">
                        {contradictions.contradictions.slice(0, 4).map(c => (
                          <div key={c.id} className="conflict">
                            <div className="conflict-head">{c.conflicting_terms ? c.conflicting_terms.join(' vs ') : c.metric || 'Value conflict'}</div>
                            <div className="conflict-expl">{c.explanation}</div>
                            <div className="conflict-grid">
                              {(c.evidence_refs || []).slice(0, 2).map((r, idx) => (
                                <div key={idx} className="conflict-source">
                                  <div className="src-label">Source {String.fromCharCode(65 + idx)}</div>
                                  <div className="src-file">{r.filename}</div>
                                  <div className="src-meta">{r.chunk_id?.slice(0, 22)} · page {r.page_number ?? '—'}</div>
                                </div>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    </section>
                  )}

                  {/* Evidence preview + graph + audit inline */}
                  <div className="two-col">
                    <section className="card">
                      <div className="card-head"><h3>Supporting evidence</h3><button className="btn ghost sm" onClick={() => setView('evidence')}>View all</button></div>
                      {filteredEvidence.length === 0 ? <div className="empty-text">No evidence items.</div> : (
                        <div className="evidence-list">
                          {filteredEvidence.slice(0, 4).map((r) => (
                            <button key={r.chunk_id} className="evidence-card" onClick={() => { setSelectedEvidence(r); setView('evidence') }}>
                              <div className="ev-head"><span className="ev-file">{r.filename}</span>{r.page_number ? <span className="ev-page">p. {r.page_number}</span> : null}<span className="ev-score">{r.score != null ? r.score.toFixed(3) : ''}</span></div>
                              <div className="ev-excerpt">{r.text_snippet || r.chunk_id}</div>
                            </button>
                          ))}
                        </div>
                      )}
                    </section>
                    <section className="card">
                      <div className="card-head"><h3>Evidence graph</h3><button className="btn ghost sm" onClick={() => setView('evidence')}>Open graph</button></div>
                      {!graph ? <div className="empty-text">Graph will appear after investigation completes.</div> : (
                        <>
                          <div className="kv"><span>Nodes</span><strong>{graph.nodes?.length}</strong></div>
                          <div className="kv"><span>Relationships</span><strong>{graph.edges?.length}</strong></div>
                          <div className="graph-mini">
                            {graph.nodes?.slice(0, 8).map(n => (
                              <span key={n.id} className={`node-pill ${n.type}`}>{n.type}</span>
                            ))}
                            {graph.nodes?.length > 8 && <span className="more">+{graph.nodes.length - 8} more</span>}
                          </div>
                        </>
                      )}
                    </section>
                  </div>
                </>
              )}
            </div>
          )}

          {/* DOCUMENTS */}
          {view === 'documents' && (
            <div className="stack">
              <div className="page-head">
                <h1>Documents</h1>
                <p>Indexed sources available for investigation. Upload additional files or search the library.</p>
              </div>

              <div className="toolbar">
                <div className="search">
                  <Icon name="search" /><input value={docSearch} onChange={e => setDocSearch(e.target.value)} placeholder="Search by filename" />
                </div>
                <button className="btn primary" onClick={() => fileRef.current?.click()} disabled={uploading}><Icon name="upload" /> {uploading ? 'Uploading…' : 'Upload files'}</button>
                <input ref={fileRef} type="file" hidden onChange={onFileInput} accept=".pdf,.csv,.json,.txt,.log,.md,.png,.jpg,.jpeg,.webp" multiple />
              </div>

              <div
                className={`card dropzone ${dragOver ? 'over' : ''}`}
                onDragOver={e => { e.preventDefault(); setDragOver(true) }}
                onDragLeave={() => setDragOver(false)}
                onDrop={e => { e.preventDefault(); setDragOver(false); onUploadFiles(e.dataTransfer.files) }}
              >
                <div className="drop-inner">
                  <Icon name="upload" size={20} />
                  <div><strong>Drag and drop files here</strong><div className="hint">PDF, CSV, JSON, TXT, LOG, MD, images — indexed locally</div></div>
                </div>
              </div>

              <section className="card">
                <div className="card-head"><h3>Library</h3><span className="count">{filteredDocs.length} files</span></div>
                {filteredDocs.length === 0 ? (
                  <div className="empty">
                    <div className="empty-title">No matching documents</div>
                    <div className="empty-text">Try a different search or upload new files. Sample data includes SOP, sensor logs and maintenance records.</div>
                  </div>
                ) : (
                  <div className="doc-grid">
                    {filteredDocs.map(d => (
                      <div key={d.id} className="doc-card">
                        <div className="doc-head">
                          <span className="doc-type">{d.file_type}</span>
                          <span className={`doc-status ${d.page_count ? 'ok' : 'muted'}`}>{d.page_count ? 'Indexed' : 'Available'}</span>
                        </div>
                        <div className="doc-name" title={d.filename}>{d.filename}</div>
                        <div className="doc-meta">{d.page_count ? `${d.page_count} pages` : '—'} · {d.sha256 ? d.sha256.slice(0, 12) : ''}</div>
                        <div className="doc-foot"><span className="hint">{fmtDate(d.ingested_at)}</span></div>
                      </div>
                    ))}
                  </div>
                )}
              </section>
            </div>
          )}

          {/* EVIDENCE */}
          {view === 'evidence' && (
            <div className="stack">
              <div className="page-head">
                <h1>Evidence</h1>
                <p>Every finding is traceable to source segments. Inspect excerpts, relevance and provenance.</p>
              </div>

              {!current ? (
                <div className="card empty">
                  <div className="empty-title">No investigation selected</div>
                  <div className="empty-text">Run an investigation to generate evidence. Evidence cards will show file, location, excerpt and provenance.</div>
                  <button className="btn primary" onClick={() => setView('investigations')}>Go to investigations</button>
                </div>
              ) : (
                <>
                  <div className="toolbar">
                    <div className="tabs">
                      <button className={`tab ${evidenceFilter === 'all' ? 'active' : ''}`} onClick={() => setEvidenceFilter('all')}>All evidence ({evidenceList.length})</button>
                      <button className={`tab ${evidenceFilter === 'high' ? 'active' : ''}`} onClick={() => setEvidenceFilter('high')}>High relevance</button>
                    </div>
                    <span className="hint">{filteredEvidence.length} items · {current.evidence_state}</span>
                  </div>

                  {contradictions?.has_contradictions && (
                    <section className="card">
                      <div className="card-head"><h3>Evidence conflicts</h3><StatusBadge state="CONFLICTING_EVIDENCE" /></div>
                      <div className="conflicts">
                        {contradictions.contradictions.slice(0, 3).map(c => (
                          <div key={c.id} className="conflict">
                            <div className="conflict-head">{c.conflicting_terms ? c.conflicting_terms.join(' vs ') : 'Conflict'}</div>
                            <div className="conflict-expl">{c.explanation}</div>
                            <div className="conflict-grid">
                              {(c.evidence_refs || []).slice(0, 2).map((r, i) => (
                                <div key={i} className="conflict-source">
                                  <div className="src-file">{r.filename}</div>
                                  <div className="src-meta">{r.chunk_id}</div>
                                </div>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    </section>
                  )}

                  <div className="evidence-grid">
                    {filteredEvidence.map(r => (
                      <div key={r.chunk_id} className={`evidence-card ${selectedEvidence?.chunk_id === r.chunk_id ? 'selected' : ''}`} onClick={() => setSelectedEvidence(r)}>
                        <div className="ev-head">
                          <span className="ev-file">{r.filename}</span>
                          {r.page_number ? <span className="ev-page">p. {r.page_number}</span> : null}
                          <span className="ev-score" title="Relevance">{r.score != null ? r.score.toFixed(3) : ''}</span>
                        </div>
                        <div className="ev-excerpt">{r.text_snippet || r.chunk_id}</div>
                        <div className="ev-foot">
                          <span className="hint">{r.chunk_id.slice(0, 24)}…</span>
                          <button className="btn ghost sm" onClick={(e) => { e.stopPropagation(); setSelectedEvidence(r) }}>Details</button>
                        </div>
                        {selectedEvidence?.chunk_id === r.chunk_id && (
                          <div className="ev-details">
                            <div className="kv"><span>Source</span><strong>{r.source_path || r.filename}</strong></div>
                            <div className="kv"><span>SHA-256</span><strong style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11 }}>{r.sha256 || '—'}</strong></div>
                            <div className="kv"><span>Chunk</span><strong>{r.chunk_id}</strong></div>
                            <div className="kv"><span>Page / Score</span><strong>{r.page_number ?? '—'} / {r.score != null ? r.score.toFixed(3) : '—'}</strong></div>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>

                  {/* Graph */}
                  <section className="card">
                    <div className="card-head"><h3>Evidence graph</h3><span className="hint">{graph ? `${graph.nodes?.length} nodes · ${graph.edges?.length} relationships` : 'No graph yet'}</span></div>
                    {!graph ? <div className="empty-text">Run an investigation to build the graph: Investigation → Steps → Evidence → Documents.</div> : (
                      <div className="graph-layout">
                        <div className="graph-canvas">
                          <div className="graph-legend">
                            <span className="node-pill investigation">Investigation</span>
                            <span className="node-pill step">Step</span>
                            <span className="node-pill tool">Tool</span>
                            <span className="node-pill document">Document</span>
                            <span className="node-pill chunk">Segment</span>
                          </div>
                          <div className="graph-nodes">
                            {graph.nodes?.slice(0, 18).map(n => (
                              <div key={n.id} className={`graph-node ${n.type}`} title={n.label}>
                                <span className="gn-type">{n.type}</span>
                                <span className="gn-label">{n.label.slice(0, 36)}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                        <div className="graph-edges">
                          <div className="hint">Relationships</div>
                          <div className="edge-list">
                            {graph.edges?.slice(0, 12).map(e => (
                              <div key={e.id} className="edge-row"><span className="edge-from">{e.from_node.slice(0, 18)}</span><Icon name="chevron" size={12} /><span className="edge-rel">{e.relation}</span><Icon name="chevron" size={12} /><span className="edge-to">{e.to_node.slice(0, 18)}</span></div>
                            ))}
                          </div>
                        </div>
                      </div>
                    )}
                  </section>
                </>
              )}
            </div>
          )}

          {/* AUDIT */}
          {view === 'audit' && (
            <div className="stack">
              <div className="page-head">
                <h1>Audit Trail</h1>
                <p>Every action is logged for review. Expand an entry for technical details.</p>
              </div>

              {!current ? (
                <div className="card empty">
                  <div className="empty-title">No investigation selected</div>
                  <div className="empty-text">Select or run an investigation to view its audit trail.</div>
                </div>
              ) : (
                <section className="card">
                  <div className="card-head"><h3>Events</h3><span className="hint">{audit.length} entries · {shortId(current.investigation_id)}</span></div>
                  {audit.length === 0 ? <div className="empty-text">No events recorded.</div> : (
                    <div className="audit-list">
                      {audit.slice().reverse().map(ev => {
                        const labelMap = {
                          investigation_start: 'Investigation started',
                          plan_generated: 'Plan generated',
                          tool_call: ev.tool ? `${ev.tool.replace(/_/g, ' ')}` : 'Tool call',
                          evidence_state: 'Evidence evaluated',
                          investigation_complete: 'Investigation completed',
                          safety_rejection: 'Safety check',
                        }
                        const label = labelMap[ev.event_type] || ev.event_type
                        const ok = ev.success === 1 ? 'ok' : ev.success === 0 ? 'fail' : 'muted'
                        return (
                          <details key={ev.id} className="audit-row">
                            <summary>
                              <span className="audit-time">{fmtTime(ev.timestamp)}</span>
                              <span className="audit-label">{label}</span>
                              <span className={`audit-status ${ok}`}>{ok === 'ok' ? 'Completed' : ok === 'fail' ? 'Attention' : '—'}</span>
                              <span className="audit-tool">{ev.tool || ''}</span>
                            </summary>
                            <div className="audit-details">
                              <div className="kv"><span>Event</span><strong>{ev.event_type}</strong></div>
                              {ev.tool && <div className="kv"><span>Tool</span><strong>{ev.tool}</strong></div>}
                              {ev.error_code && <div className="kv"><span>Code</span><strong>{ev.error_code}</strong></div>}
                              <div className="kv"><span>Time</span><strong>{fmtDate(ev.timestamp)}</strong></div>
                              {ev.execution_ms != null && <div className="kv"><span>Duration</span><strong>{ev.execution_ms} ms</strong></div>}
                            </div>
                          </details>
                        )
                      })}
                    </div>
                  )}
                </section>
              )}
            </div>
          )}

          {/* SETTINGS */}
          {view === 'settings' && (
            <div className="stack">
              <div className="page-head">
                <h1>Settings</h1>
                <p>Local configuration. Values are read-only in this build.</p>
              </div>
              <div className="two-col">
                <section className="card">
                  <div className="card-head"><h3>Processing</h3></div>
                  <div className="settings">
                    <div className="kv"><span>Mode</span><strong>Local only</strong></div>
                    <div className="kv"><span>Provider</span><strong>{health?.mock_default ? 'Local model' : 'Local model'}</strong></div>
                    <div className="kv"><span>Evidence validation</span><strong>Active</strong></div>
                    <div className="kv"><span>Audit logging</span><strong>Active</strong></div>
                    <div className="kv"><span>Cloud access</span><strong>Disabled</strong></div>
                  </div>
                </section>
                <section className="card">
                  <div className="card-head"><h3>Retrieval</h3></div>
                  <div className="settings">
                    <div className="kv"><span>Documents</span><strong>{health?.documents ?? 0}</strong></div>
                    <div className="kv"><span>Segments</span><strong>{health?.chunks ?? 0}</strong></div>
                    <div className="kv"><span>Index</span><strong>Local vector store</strong></div>
                    <div className="kv"><span>Search</span><strong>Local embeddings</strong></div>
                  </div>
                </section>
              </div>
              <section className="card">
                <div className="card-head"><h3>Safety</h3></div>
                <div className="settings">
                  <div className="kv"><span>Provenance</span><strong>SHA-256 per source</strong></div>
                  <div className="kv"><span>Path validation</span><strong>Enabled</strong></div>
                  <div className="kv"><span>Tool allowlist</span><strong>Enabled</strong></div>
                  <div className="kv"><span>Prompt protection</span><strong>Enabled</strong></div>
                </div>
                <div className="hint" style={{ marginTop: 12 }}>Sensitive values are not displayed. Configuration is managed via local environment.</div>
              </section>
            </div>
          )}

          <footer className="footer">
            <span>SIH26117 Sovereign Workbench — Local processing · Evidence-backed · Traceable</span>
            <span className="hint">Offline operation · All data remains on this system</span>
          </footer>
        </main>
      </div>
    </div>
  )
}
