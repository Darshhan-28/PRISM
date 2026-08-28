import { useEffect, useState } from 'react'

const API = '/api'

function Badge({ state }) {
  const colors = {
    SUPPORTED: '#0a7a42',
    PARTIALLY_SUPPORTED: '#b58900',
    INSUFFICIENT_EVIDENCE: '#b00020',
    CONFLICTING_EVIDENCE: '#a020a0'
  }
  return <span style={{background: colors[state]||'#555', color:'#fff', padding:'2px 8px', borderRadius:12, fontSize:12, fontWeight:700}}>{state}</span>
}

export default function App() {
  const [health, setHealth] = useState(null)
  const [docs, setDocs] = useState([])
  const [objective, setObjective] = useState('Investigate abnormal pressure event in Pump P-204 on 2026-08-15')
  const [invest, setInvest] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [graph, setGraph] = useState(null)
  const [audit, setAudit] = useState([])
  const [contradictions, setContradictions] = useState(null)
  const [queryAns, setQueryAns] = useState(null)

  const fetchHealth = async () => {
    const r = await fetch(`${API}/health`)
    if (r.ok) setHealth(await r.json())
    const d = await fetch(`${API}/documents`).then(x=>x.json()).catch(()=>({documents:[]}))
    setDocs(d.documents||[])
  }
  useEffect(()=>{ fetchHealth() },[])

  const runInvestigation = async (obj) => {
    const target = obj || objective
    if (!target || target.length < 10) { setError('Objective too short (min 10 chars)'); return }
    setLoading(true); setError('')
    try {
      const r = await fetch(`${API}/investigations`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({objective: target})})
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText)
      const data = await r.json()
      setInvest(data)
      // graph + audit
      const g = await fetch(`${API}/investigations/${data.investigation_id}/graph`).then(x=>x.json()).catch(()=>null)
      setGraph(g)
      const a = await fetch(`${API}/investigations/${data.investigation_id}/audit`).then(x=>x.json()).catch(()=>({events:[]}))
      setAudit(a.events||[])
      // contradictions via query
      const q = await fetch(`${API}/query`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({query: target})}).then(x=>x.json()).catch(()=>null)
      if (q) setContradictions(q.contradictions)
      setQueryAns(q)
      fetchHealth()
    } catch(e){ setError(String(e.message||e)) }
    finally { setLoading(false)}
  }

  const onUpload = async (e) => {
    const file = e.target.files[0]
    if (!file) return
    const fd = new FormData()
    fd.append('file', file)
    setError('')
    try {
      const r = await fetch(`${API}/ingest`, {method:'POST', body: fd})
      if (!r.ok) throw new Error((await r.json()).detail || 'ingest failed')
      await r.json()
      fetchHealth()
    } catch(err){ setError(String(err.message||err)) }
  }

  return (
    <div style={{fontFamily:'system-ui, -apple-system, Segoe UI, Roboto, sans-serif', margin:0, background:'#f6f7f9', minHeight:'100vh', color:'#1a1a1a'}}>
      <header style={{background:'#0f172a', color:'#fff', padding:'16px 20px', display:'flex', justifyContent:'space-between', alignItems:'center'}}>
        <div>
          <div style={{fontWeight:800, fontSize:18}}>SIH26117 — Sovereign Workbench</div>
          <div style={{fontSize:12, opacity:0.8}}>Offline • Evidence-backed • Audit Trail • Mock default</div>
        </div>
        <div style={{fontSize:12, opacity:0.9}}>Phase 12 Demo • 16 GB / Iris Xe • No Cloud</div>
      </header>

      <div style={{maxWidth:1200, margin:'0 auto', padding:16, display:'grid', gap:16}}>
        {/* Dashboard */}
        <section style={{background:'#fff', borderRadius:12, padding:16, boxShadow:'0 1px 6px rgba(0,0,0,0.08)'}}>
          <h3 style={{margin:'0 0 12px 0'}}>Dashboard</h3>
          <div style={{display:'grid', gridTemplateColumns:'repeat(auto-fit, minmax(180px, 1fr))', gap:12}}>
            <div style={{border:'1px solid #e5e7eb', borderRadius:8, padding:12}}>
              <div style={{fontSize:12, color:'#6b7280'}}>Documents</div>
              <div style={{fontSize:22, fontWeight:700}}>{health?.documents ?? '-'}</div>
              <div style={{fontSize:12, color:'#0a7a42'}}>chunks: {health?.chunks ?? '-'}</div>
            </div>
            <div style={{border:'1px solid #e5e7eb', borderRadius:8, padding:12}}>
              <div style={{fontSize:12, color:'#6b7280'}}>Investigations</div>
              <div style={{fontSize:22, fontWeight:700}}>{health?.investigations ?? '-'}</div>
              <div style={{fontSize:12, color: invest?.evidence_state==='CONFLICTING_EVIDENCE'?'#a020a0':'#0a7a42'}}>{invest?.evidence_state || '—'}</div>
            </div>
            <div style={{border:'1px solid #e5e7eb', borderRadius:8, padding:12}}>
              <div style={{fontSize:12, color:'#6b7280'}}>Vector Store</div>
              <div style={{fontSize:22, fontWeight:700}}>{health?.vector_store ?? '-'}</div>
              <div style={{fontSize:12}}>offline: {health?.offline ? 'yes' : '—'}</div>
            </div>
            <div style={{border:'1px solid #e5e7eb', borderRadius:8, padding:12}}>
              <div style={{fontSize:12, color:'#6b7280'}}>Safety</div>
              <div style={{fontSize:12}}>mock_default • no cloud • safety gated</div>
              <div style={{fontSize:12, color:'#0a7a42'}}>audit: {audit.length} events</div>
            </div>
          </div>
        </section>

        {/* Evidence Workspace */}
        <section style={{background:'#fff', borderRadius:12, padding:16, boxShadow:'0 1px 6px rgba(0,0,0,0.08)'}}>
          <h3 style={{margin:'0 0 8px 0'}}>Evidence Workspace</h3>
          <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:16}}>
            <div>
              <div style={{fontSize:13, fontWeight:600, marginBottom:6}}>1) Local Documents</div>
              <input type="file" onChange={onUpload} accept=".pdf,.csv,.json,.txt,.log,.md,.png,.jpg,.jpeg,.webp" />
              <div style={{maxHeight:180, overflow:'auto', border:'1px solid #e5e7eb', borderRadius:8, marginTop:8, padding:8, fontSize:12}}>
                {docs.length===0? <div style={{color:'#6b7280'}}>No documents yet — synthetic samples pre-loaded.</div> : docs.slice(0,12).map(d=> <div key={d.id} style={{display:'flex', justifyContent:'space-between'}}><span>{d.filename}</span><span style={{color:'#6b7280'}}>{d.file_type}</span></div>)}
              </div>
              <div style={{fontSize:12, color:'#6b7280', marginTop:6}}>Offline at `data/raw/samples` — SOP_P-204.pdf, maintenance logs, sensor JSON, etc. work immediately.</div>
            </div>
            <div>
              <div style={{fontSize:13, fontWeight:600, marginBottom:6}}>2) Investigation Question</div>
              <textarea value={objective} onChange={e=>setObjective(e.target.value)} rows={3} style={{width:'100%', padding:8, borderRadius:8, border:'1px solid #e5e7eb'}} placeholder="Investigate..." />
              <div style={{display:'flex', gap:8, marginTop:8, flexWrap:'wrap'}}>
                <button onClick={()=>runInvestigation()} disabled={loading} style={{background:'#0f172a', color:'#fff', border:'none', padding:'8px 14px', borderRadius:8, cursor:'pointer'}}>{loading?'Running...':'Run Investigation'}</button>
                <button onClick={()=>runInvestigation('Investigate valve replacement status for P-204')} style={{background:'#e5e7eb', border:'none', padding:'8px 12px', borderRadius:8, cursor:'pointer'}}>Try: valve status</button>
                <button onClick={()=>runInvestigation('Investigate vibration anomaly P-204')} style={{background:'#e5e7eb', border:'none', padding:'8px 12px', borderRadius:8, cursor:'pointer'}}>Try: vibration</button>
              </div>
              {error && <div style={{color:'#b00020', fontSize:13, marginTop:8}}>{error}</div>}
            </div>
          </div>
          {invest && (
            <div style={{marginTop:16, borderTop:'1px solid #e5e7eb', paddingTop:12}}>
              <div style={{display:'flex', gap:8, alignItems:'center', flexWrap:'wrap'}}>
                <strong>Answer</strong> <Badge state={invest.evidence_state} />
                <span style={{fontSize:12, color:'#6b7280'}}>{invest.investigation_id.slice(0,8)} • {invest.status}</span>
              </div>
              <div style={{background:'#f9fafb', border:'1px solid #e5e7eb', borderRadius:8, padding:12, marginTop:8, fontSize:14}}>{invest.summary}</div>
              <div style={{fontSize:12, color:'#6b7280', marginTop:6}}>Steps: {invest.steps_executed?.length||0} • Evidence refs: {invest.evidence_refs?.length||0} • Evidence engine: {invest.evidence_state}</div>
            </div>
          )}
        </section>

        {/* Evidence + Contradiction + Graph + Audit */}
        {invest && (
          <>
            <section style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:16}}>
              <div style={{background:'#fff', borderRadius:12, padding:16, boxShadow:'0 1px 6px rgba(0,0,0,0.08)'}}>
                <h4 style={{margin:'0 0 8px 0'}}>Evidence Panel</h4>
                <div style={{display:'grid', gap:8}}>
                  {(invest.evidence_refs||[]).slice(0,6).map((r,i)=> (
                    <div key={i} style={{border:'1px solid #e5e7eb', borderRadius:8, padding:8}}>
                      <div style={{fontWeight:600, fontSize:13}}>{r.filename} {r.page_number?`• p${r.page_number}`:''} {r.score?`• score ${r.score.toFixed(3)}`:''}</div>
                      <div style={{fontSize:12, color:'#374151'}}>{r.text_snippet || r.chunk_id}</div>
                      <div style={{fontSize:11, color:'#6b7280', wordBreak:'break-all'}}>{r.chunk_id} • {r.sha256?.slice(0,12)} • {r.source_path?.split('/').pop()}</div>
                    </div>
                  ))}
                  {(!invest.evidence_refs||invest.evidence_refs.length===0) && <div style={{color:'#6b7280', fontSize:13}}>No evidence refs.</div>}
                </div>
              </div>
              <div style={{background:'#fff', borderRadius:12, padding:16, boxShadow:'0 1px 6px rgba(0,0,0,0.08)'}}>
                <h4 style={{margin:'0 0 8px 0'}}>Contradiction Panel</h4>
                {contradictions?.has_contradictions ? (
                  <div style={{border:'1px solid #e5e7eb', borderRadius:8, padding:8}}>
                    <div style={{color:'#a020a0', fontWeight:700, fontSize:13}}>CONFLICT DETECTED — {contradictions.contradictions.length} contradiction(s)</div>
                    {contradictions.contradictions.slice(0,3).map(c=> (
                      <div key={c.id} style={{marginTop:8, fontSize:13}}>
                        <div style={{fontWeight:600}}>{c.conflicting_terms ? c.conflicting_terms.join(' vs ') : c.metric}</div>
                        <div style={{color:'#374151'}}>{c.explanation}</div>
                        <div style={{fontSize:11, color:'#6b7280'}}>{c.evidence_refs?.map(r=>r.filename).join(' ↔ ')}</div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div style={{border:'1px dashed #e5e7eb', borderRadius:8, padding:12, fontSize:13, color:'#6b7280'}}>No contradictions detected for this investigation. If two sources disagree (e.g., “valve replaced” vs “pending”), they appear here with <code>CONFLICTING_EVIDENCE</code>.</div>
                )}
                <div style={{marginTop:8, fontSize:12, color:'#6b7280'}}>Evidence state: <Badge state={invest.evidence_state} /></div>
              </div>
            </section>

            <section style={{background:'#fff', borderRadius:12, padding:16, boxShadow:'0 1px 6px rgba(0,0,0,0.08)'}}>
              <h4 style={{margin:'0 0 8px 0'}}>Evidence Graph (Investigation → Steps → Tools → Evidence → Documents → Chunks)</h4>
              {!graph ? <div style={{color:'#6b7280'}}>No graph.</div> : (
                <div>
                  <div style={{fontSize:12, color:'#6b7280'}}>Nodes: {graph.nodes?.length} • Edges: {graph.edges?.length}</div>
                  <div style={{display:'flex', gap:6, flexWrap:'wrap', marginTop:8}}>
                    {graph.nodes?.slice(0,30).map(n=> (
                      <span key={n.id} style={{border:'1px solid #e5e7eb', borderRadius:16, padding:'4px 8px', fontSize:11, background: n.type==='investigation'?'#dbeafe': n.type==='step'?'#fef3c7': n.type==='tool'?'#ede9fe': n.type==='document'?'#dcfce7': n.type==='chunk'?'#fee2e2':'#f3f4f6'}}>{n.type}: {n.label.slice(0,24)}</span>
                    ))}
                  </div>
                  <div style={{maxHeight:160, overflow:'auto', border:'1px solid #e5e7eb', borderRadius:8, marginTop:8, padding:8, fontSize:11}}>
                    {graph.edges?.slice(0,20).map(e=> <div key={e.id}>{e.from_node.slice(0,20)} —<span style={{color:'#6b7280'}}>{e.relation}</span>→ {e.to_node.slice(0,20)}</div>)}
                  </div>
                </div>
              )}
            </section>

            <section style={{background:'#fff', borderRadius:12, padding:16, boxShadow:'0 1px 6px rgba(0,0,0,0.08)'}}>
              <h4 style={{margin:'0 0 8px 0'}}>Audit / Safety Panel</h4>
              <div style={{maxHeight:200, overflow:'auto', border:'1px solid #e5e7eb', borderRadius:8, padding:8, fontSize:11}}>
                {audit.length===0? <div style={{color:'#6b7280'}}>No events.</div> : audit.slice(-12).reverse().map(a=> (
                  <div key={a.id} style={{display:'grid', gridTemplateColumns:'140px 120px 1fr', gap:8, padding:'4px 0', borderBottom:'1px solid #f3f4f6'}}>
                    <span style={{color:'#6b7280'}}>{new Date(a.timestamp).toLocaleTimeString()}</span>
                    <span style={{fontWeight:600}}>{a.event_type}</span>
                    <span>{a.tool||''} {a.error_code?`⚠ ${a.error_code}`:''} {a.success===1?'✓':a.success===0?'✗':''}</span>
                  </div>
                ))}
              </div>
              <div style={{fontSize:12, color:'#6b7280', marginTop:6}}>Append-only • sanitized • offline • investigation {invest.investigation_id.slice(0,8)}</div>
            </section>
          </>
        )}

        <footer style={{fontSize:12, color:'#6b7280', textAlign:'center', padding:8}}>
          Demo: Offline by default • MockAdapter active • Replace model via config without code change • SHA-256 provenance preserved
        </footer>
      </div>
    </div>
  )
}
