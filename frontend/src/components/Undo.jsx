import { useEffect, useState } from 'react'
import { api } from '../api.js'

export default function Undo({ undo, refund }) {
  const [left, setLeft] = useState(0)
  const [err, setErr] = useState('')
  useEffect(() => {
    if (!undo || undo.status !== 'pending') return
    const tick = () => setLeft(Math.max(0, Math.ceil((new Date(undo.finalizeAt) - Date.now()) / 1000)))
    tick()
    const t = setInterval(tick, 250)
    return () => clearInterval(t)
  }, [undo])
  if (!undo) return null

  const doUndo = async () => {
    setErr('')
    try { await api(`/api/undo/${undo.decisionId}`, { method: 'POST' }) } catch (e) { setErr(e.message) }
  }
  return (
    <div className="card">
      <h3>Undo window — money moves last</h3>
      {undo.status === 'pending' && (
        <div className="row">
          <div className="countdown">{left}s</div>
          <div className="muted">Nothing has been refunded yet.</div>
          <div className="spacer" />
          <button className="btn danger" onClick={doUndo}>Undo refund</button>
        </div>
      )}
      {undo.status === 'undone' && <div>↩ Refund undone. The payment provider was never called.</div>}
      {undo.status === 'expired' && !refund && <div className="muted">Window closed — issuing refund…</div>}
      {refund?.ok && (
        <div>
          ✅ {refund.provider === 'simulated' ? 'Simulated refund' : 'Refund issued via Dodo Payments (test mode)'}
          <dl className="kv" style={{ marginTop: 8 }}>
            <dt>refund_id</dt><dd>{refund.refund_id}</dd>
            <dt>amount</dt><dd>{refund.amount} {refund.currency}</dd>
            <dt>status</dt><dd>{refund.status}</dd>
          </dl>
        </div>
      )}
      {refund && !refund.ok && <div className="err">Refund failed: {refund.error}. A specialist will follow up.</div>}
      {err && <div className="err">{err}</div>}
    </div>
  )
}
