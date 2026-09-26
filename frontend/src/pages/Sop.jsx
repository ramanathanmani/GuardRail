import { useEffect, useState } from 'react'
import { api } from '../api.js'

const num = (v) => (v === '' ? 0 : Number(v))

export default function Sop() {
  const [sop, setSop] = useState(null)
  const [msg, setMsg] = useState('')
  useEffect(() => { api('/api/sop').then(setSop).catch((e) => setMsg(e.message)) }, [])
  if (!sop) return <div className="card empty">{msg || 'Loading…'}</div>
  const set = (k, v) => { setSop({ ...sop, [k]: v }); setMsg('') }
  const save = async () => {
    try { setSop(await api('/api/sop', { method: 'PUT', body: sop })); setMsg('Saved. The next call uses these rules.') }
    catch (e) { setMsg('Error: ' + e.message) }
  }
  const Field = ({ label, hint, children }) => (
    <div><label><b>{label}</b></label><div className="muted" style={{ marginBottom: 4 }}>{hint}</div>{children}</div>
  )
  return (
    <div className="card" style={{ maxWidth: 720 }}>
      <h3>SOP rules — edits apply to the very next call</h3>
      <div className="stack">
        <Field label="Return window (days)" hint="Refunds and replacements are refused after this many days.">
          <input type="number" value={sop.return_window_days} onChange={(e) => set('return_window_days', num(e.target.value))} />
        </Field>
        <Field label="Max refunds per window" hint="Refund limit; refunds only. Over the limit, the agent offers an exchange.">
          <input type="number" value={sop.max_refunds} onChange={(e) => set('max_refunds', num(e.target.value))} />
        </Field>
        <Field label="Refund window (days)" hint="How far back refunds are counted.">
          <input type="number" value={sop.refund_window_days} onChange={(e) => set('refund_window_days', num(e.target.value))} />
        </Field>
        <Field label="Excluded categories" hint="Comma-separated; these can't be refunded or replaced.">
          <input type="text" value={sop.excluded_categories.join(', ')}
            onChange={(e) => set('excluded_categories', e.target.value.split(',').map((s) => s.trim()).filter(Boolean))} />
        </Field>
        <Field label="High-value hold (₹)" hint="Refunds above this always open an undo window and are tagged high-value-hold.">
          <input type="number" value={sop.high_value_hold_inr} onChange={(e) => set('high_value_hold_inr', num(e.target.value))} />
        </Field>
        <div className="toggle"><div><b>Confirm on negation</b><div className="muted">Ask the customer first when negation or conflicting actions are detected.</div></div>
          <button className={'switch' + (sop.require_confirm_on_negation ? ' on' : '')} onClick={() => set('require_confirm_on_negation', !sop.require_confirm_on_negation)} /></div>
        <div className="toggle"><div><b>Undo window</b><div className="muted">Hold every refund briefly so it can be cancelled.</div></div>
          <button className={'switch' + (sop.undo_window_enabled ? ' on' : '')} onClick={() => set('undo_window_enabled', !sop.undo_window_enabled)} /></div>
        <div className="row"><button className="btn" onClick={save}>Save rules</button><span className={msg.startsWith('Error') ? 'err' : 'muted'}>{msg}</span></div>
      </div>
    </div>
  )
}
