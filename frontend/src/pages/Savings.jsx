import { useEffect, useState } from 'react'
import { api } from '../api.js'

const LANGS = { hi: 'Hindi', ta: 'Tamil', kn: 'Kannada' }

export default function Savings() {
  const [s, setS] = useState(null)
  useEffect(() => { api('/api/stats').then(setS).catch(() => {}) }, [])
  if (!s) return <div className="card empty">Loading…</div>
  const kinds = Object.entries(s.decisions_by_kind)
  const max = Math.max(1, ...kinds.map(([, n]) => n))
  const maxLang = Math.max(1, ...Object.values(s.negation_catches_by_language))
  return (
    <div className="stack">
      <div className="card">
        <h3>Impact {s.sample_rows > 0 && <span className="badge sample" style={{ marginLeft: 8 }}>includes {s.sample_rows} sample-history rows</span>}</h3>
        <div className="stats">
          <div className="stat"><div className="n">{s.actions_checked}</div><div className="l">actions checked</div></div>
          <div className="stat"><div className="n">{s.wrong_actions_prevented}</div><div className="l">wrong actions prevented</div></div>
          <div className="stat"><div className="n">₹{Math.round(s.money_protected_inr).toLocaleString('en-IN')}</div><div className="l">money protected</div></div>
          <div className="stat"><div className="n">{s.avg_decision_ms} ms</div><div className="l">average decision time</div></div>
        </div>
      </div>
      <div className="card">
        <h3>Decisions by kind</h3>
        {kinds.map(([k, n]) => (
          <div key={k} className="row" style={{ marginBottom: 8 }}>
            <div style={{ width: 170 }}>{k.replace(/_/g, ' ')}</div>
            <div style={{ flex: 1 }}><div className="bar" style={{ width: `${(n / max) * 100}%` }} /></div>
            <b>{n}</b>
          </div>
        ))}
      </div>
      <div className="card">
        <h3>Negation catches by language</h3>
        {Object.entries(s.negation_catches_by_language).map(([k, n]) => (
          <div key={k} className="row" style={{ marginBottom: 8 }}>
            <div style={{ width: 170 }}>{LANGS[k]}</div>
            <div style={{ flex: 1 }}><div className="bar" style={{ width: `${(n / maxLang) * 100}%`, background: 'var(--warn)' }} /></div>
            <b>{n}</b>
          </div>
        ))}
        <p className="muted" style={{ marginBottom: 0 }}>Only live calls count here; sample history has no stored transcripts.</p>
      </div>
    </div>
  )
}
