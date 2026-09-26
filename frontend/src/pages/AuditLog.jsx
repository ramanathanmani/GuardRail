import { Fragment, useEffect, useState } from 'react'
import { api } from '../api.js'
import GateTrace from '../components/GateTrace.jsx'

const KINDS = ['', 'EXECUTE', 'EXECUTE_WITH_HOLD', 'CONFIRM_FIRST', 'OFFER_ALTERNATIVE', 'HUMAN_HANDOFF', 'NEED_INFO']
const when = (t) => new Date(t.endsWith('Z') || t.includes('+') ? t : t + 'Z').toLocaleString()

export default function AuditLog() {
  const [rows, setRows] = useState(null)
  const [kind, setKind] = useState('')
  const [open, setOpen] = useState(null)
  useEffect(() => { api('/api/decisions' + (kind ? `?kind=${kind}` : '')).then(setRows).catch(() => setRows([])) }, [kind])

  return (
    <div className="card">
      <h3>Audit log</h3>
      <div className="row" style={{ marginBottom: 12 }}>
        <select style={{ width: 'auto' }} value={kind} onChange={(e) => setKind(e.target.value)}>
          {KINDS.map((k) => <option key={k} value={k}>{k || 'All decisions'}</option>)}
        </select>
        <span className="muted">{rows ? rows.length : '…'} rows</span>
      </div>
      {rows && !rows.length && <div className="empty">No decisions yet.</div>}
      <table>
        <thead><tr><th>When</th><th>Caller</th><th>Decision</th><th>Action</th><th>Refund</th><th>Ticket</th></tr></thead>
        <tbody>
          {(rows || []).map((r) => (
            <Fragment key={r.id}>
              <tr className="click" onClick={() => setOpen(open === r.id ? null : r.id)}>
                <td>{when(r.created_at)}</td>
                <td>{r.profile} <span className="muted">· {r.channel}</span> {r.is_seed && <span className="badge sample">sample history</span>}</td>
                <td><span className={'kind ' + r.kind} style={{ fontSize: 12, padding: '2px 8px' }}>{r.kind.replace(/_/g, ' ')}</span></td>
                <td>{r.action_type || '—'}{r.amount_inr ? ` · ₹${r.amount_inr}` : ''}</td>
                <td>{r.status}{r.refund_provider ? ` (${r.refund_provider === 'simulated' ? 'simulated' : 'Dodo'})` : ''}</td>
                <td>{r.ticket_url ? <a href={r.ticket_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>#{r.ticket_id}</a> : (r.ticket_id || '—')}</td>
              </tr>
              {open === r.id && (
                <tr><td colSpan={6}>
                  <div className="muted" style={{ marginBottom: 8 }}>{r.reason}{r.latency_ms ? ` · decided in ${r.latency_ms} ms` : ''}</div>
                  <GateTrace steps={r.trace_json} />
                </td></tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  )
}
