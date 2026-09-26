import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import GateTrace from '../components/GateTrace.jsx'
import Undo from '../components/Undo.jsx'
import { TicketIcon } from '../components/Icons.jsx'

const LANG = { 'ta-IN': 'Tamil', 'hi-IN': 'Hindi', 'kn-IN': 'Kannada' }
const CITY = { riya: 'Coimbatore', arjun: 'Delhi', meera: 'Bangalore', karthik: 'Chennai' }
const HINT = {
  riya: 'Jar udanjiruku. Refund vendam, replacement anuppunga.',
  arjun: 'Mujhe refund chahiye, size galat hai.',
  meera: 'Sole kithu hogide, refund beku.',
  karthik: 'Order cancel mat karo, bas address change karo.',
}

export default function LiveDemo({ live, info, profile, fault }) {
  const [simCall, setSimCall] = useState(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [pinned, setPinned] = useState(null)
  const chatEnd = useRef(null)

  const current = (pinned && live.byId[pinned]) || live.calls[0]
  useEffect(() => { chatEnd.current?.scrollIntoView({ block: 'nearest' }) }, [current?.transcript.length])

  const pick = (p) => api('/api/demo/profile', { method: 'POST', body: { profile: p } }).catch((e) => setErr(e.message))
  const toggleFault = () =>
    api('/api/demo/fault-injection', { method: 'POST', body: { mode: fault === 'flip_negation' ? 'off' : 'flip_negation' } })
      .catch((e) => setErr(e.message))

  const startSim = async () => {
    setErr(''); setBusy(true)
    try {
      const r = await api('/api/sim/start', { method: 'POST', body: { profile_id: profile === 'auto' ? 'riya' : profile } })
      setSimCall(r.call_id); setPinned(r.call_id)
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  const send = async () => {
    if (!simCall || !text.trim()) return
    setBusy(true); setErr('')
    const t = text; setText('')
    try { await api('/api/sim/turn', { method: 'POST', body: { call_id: simCall, text: t } }) }
    catch (e) { setErr(e.message); if (/unknown or ended/.test(e.message)) setSimCall(null) }
    finally { setBusy(false) }
  }
  const endSim = async () => {
    try { await api('/api/sim/end', { method: 'POST', body: { call_id: simCall } }) } catch { /* already ended */ }
    setSimCall(null)
  }
  useEffect(() => { if (current?.ended && current.id === simCall) setSimCall(null) }, [current?.ended, current?.id, simCall])

  const profiles = info?.profiles || []
  const u = current?.understanding
  const negTerms = u?.negation_analysis?.negation_terms || []

  return (
    <div className="grid">
      <div className="stack">
        <div className="card">
          <h3>Next caller</h3>
          <div className="callers">
            {profiles.map((p) => (
              <button key={p.id} className={'caller' + (profile === p.id ? ' on' : '')} onClick={() => pick(p.id)}>
                <b>{p.name}</b><small>{CITY[p.id]} · {LANG[p.language_code] || p.language_code}</small>
              </button>
            ))}
            <button className={'caller' + (profile === 'auto' ? ' on' : '')} onClick={() => pick('auto')}>
              <b>Auto</b><small>match the caller's number</small>
            </button>
          </div>
          <p className="muted" style={{ marginBottom: 0 }}>
            The next phone call {info?.phone_number ? `to ${info.phone_number} ` : ''}is bound to this profile.
          </p>
        </div>

        <div className="card">
          <h3>Demo controls</h3>
          <div className="toggle">
            <div>
              <b>Simulated ASR error</b>
              <div className="muted">Flips the first "don't want" word (vendam → venum), once per call.</div>
            </div>
            <button className={'switch' + (fault === 'flip_negation' ? ' on' : '')} onClick={toggleFault} aria-label="toggle fault injection" />
          </div>
        </div>

        <div className="card">
          <h3>Typed-text call</h3>
          {!simCall ? (
            <button className="btn" onClick={startSim} disabled={busy || !profile}>Start typed call as {profile}</button>
          ) : (
            <div className="stack" style={{ gap: 8 }}>
              <textarea rows={2} value={text} placeholder={HINT[profile] || 'Type what the customer says…'}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }} />
              <div className="row">
                <button className="btn" onClick={send} disabled={busy || !text.trim()}>Send turn</button>
                <button className="btn ghost" onClick={endSim}>Hang up</button>
              </div>
            </div>
          )}
          {!simCall && HINT[profile] && <p className="muted" style={{ marginBottom: 0 }}>Try: “{HINT[profile]}”</p>}
          {err && <div className="err">{err}</div>}
        </div>
      </div>

      <div className="stack">
        {!current && <div className="card empty">Waiting for a call. Dial the number, or start a typed call.</div>}
        {current && (
          <>
            <div className="card">
              <h3>Call</h3>
              <div className="row" style={{ flexWrap: 'wrap' }}>
                <b>{current.profile}</b>
                <span className="lbl grey">{current.channel === 'phone' ? '📞 phone' : '⌨ typed'}</span>
                <Status call={current} />
                {current.fault && <span className="badge sim">SIMULATED ASR ERROR</span>}
                <div className="spacer" />
                {live.calls.length > 1 && (
                  <select style={{ width: 'auto' }} value={current.id} onChange={(e) => setPinned(e.target.value)}>
                    {live.calls.map((c) => <option key={c.id} value={c.id}>{c.profile} · {c.channel} · {c.id.slice(-6)}</option>)}
                  </select>
                )}
              </div>
              {current.fault && (
                <p className="heard">Heard: “<b>{current.fault.original}</b>”<br />ASR error simulated as: “<b>{current.fault.flipped}</b>”</p>
              )}
              <div className="chat" style={{ marginTop: 12 }}>
                {current.transcript.map((m, i) => <div key={i} className={'bubble ' + m.who}>{m.text}</div>)}
                {current.partial && <div className="bubble customer partial">{current.partial}</div>}
                <div ref={chatEnd} />
              </div>
            </div>

            {u && (
              <div className="card">
                <h3>Claude's understanding</h3>
                <dl className="kv">
                  <dt>English</dt><dd>{u.english_translation}</dd>
                  <dt>Intent</dt><dd>{u.intent} <span className="muted">({(u.languages_detected || []).join(', ')})</span></dd>
                  <dt>Actions named</dt><dd>{(u.actions_mentioned || []).join(', ') || '—'}</dd>
                  <dt>Negation terms</dt>
                  <dd>{negTerms.length ? negTerms.map((n) => `${n.term} (${n.meaning}${n.governs ? ' → ' + n.governs : ''})`).join('; ') : 'none'}</dd>
                  <dt>Confidence</dt><dd>{u.negation_analysis?.intent_confidence}</dd>
                </dl>
              </div>
            )}

            <div className="card">
              <h3>GuardRail decision trace</h3>
              <GateTrace steps={current.gates} />
              {current.decisions.length > 1 && (
                <div className="row" style={{ marginTop: 14, flexWrap: 'wrap', gap: 6 }}>
                  <span className="muted">This call:</span>
                  {current.decisions.map((d, i) => (
                    <span key={d.decision_id || i} className="row" style={{ gap: 6 }}>
                      {i > 0 && <span className="muted">→</span>}
                      <span className={'kind ' + d.kind} style={{ fontSize: 11, padding: '2px 8px' }}>{d.kind.replace(/_/g, ' ')}</span>
                    </span>
                  ))}
                </div>
              )}
              {current.decision && (
                <div className="decision" style={{ marginTop: 14 }}>
                  <span className={'kind ' + current.decision.kind}>{current.decision.kind.replace(/_/g, ' ')}</span>
                  {current.decision.action_type && <b>{current.decision.action_type.replace('_', ' ')}{current.decision.amount_inr ? ` · ₹${current.decision.amount_inr}` : ''}</b>}
                  <span className="muted">{current.decision.reason}</span>
                </div>
              )}
            </div>

            <Undo undo={current.undo} refund={current.refund} />

            {current.ticket && (
              <div className="card">
                <h3>Ticket</h3>
                <div className="row" style={{ flexWrap: 'wrap' }}>
                  {current.ticket.url
                    ? <><span className="muted">Freshdesk ticket</span><a className="ticket-id" href={current.ticket.url} target="_blank" rel="noreferrer"><TicketIcon />#{current.ticket.id} ↗</a></>
                    : <b>Ticket {current.ticket.id}</b>}
                  <div className="tags">{(current.ticket.tags || []).map((t) => <span key={t} className="tag">{t}</span>)}</div>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

const STATUS = {
  GREETING: ['Greeting', 'speak'], LISTENING: ['Listening', 'listen'], NEED_INFO: ['Listening', 'listen'],
  CONFIRMING: ['Waiting for confirmation', 'listen'], OFFERING_ALT: ['Waiting for an answer', 'listen'],
  THINKING: ['Thinking', 'think'], EXECUTING: ['Executing', 'think'], UNDO_WINDOW: ['Undo window open', 'hold'],
  CLOSING: ['Closing', 'speak'], ENDED: ['Ended', 'off'],
}

function Status({ call }) {
  if (call.ended) return <span className="status off">● Call ended{call.durationS ? ` · ${Math.round(call.durationS)}s` : ''}</span>
  if (call.speaking) return <span className="status speak">● Agent speaking</span>
  const [label, cls] = STATUS[call.state] || [call.state, 'off']
  return <span className={'status ' + cls}>● {label}</span>
}
