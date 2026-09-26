import { useEffect, useReducer, useRef, useState } from 'react'
import { wsUrl } from './api.js'

const blankCall = (id) => ({
  id, profile: '?', channel: '', state: 'GREETING', ended: false, transcript: [], partial: '',
  fault: null, understanding: null, gates: {}, decision: null, ticket: null,
  undo: null, refund: null, startedAt: Date.now(), durationS: null, decisions: [], speaking: false,
})

function reduce(calls, ev) {
  if (ev.type === 'hello') {
    return ev.recent.reduce(reduce, calls)
  }
  const id = ev.call_id
  if (!id) return calls
  const c = calls[id] || blankCall(id)
  const n = { ...c }
  switch (ev.type) {
    case 'call.started':
      Object.assign(n, blankCall(id), { profile: ev.profile, channel: ev.channel }); break
    case 'state': n.state = ev.state; break
    case 'transcript.partial': n.partial = ev.text; break
    case 'transcript.final':
      n.partial = ''; n.transcript = [...c.transcript, { who: 'customer', text: ev.text }]; break
    case 'fault.injected': n.fault = { original: ev.original, flipped: ev.flipped }; break
    case 'understanding': n.understanding = ev; break
    case 'gate':
      // step 1 starts a new turn's trace
      n.gates = ev.step === 1 ? { 1: ev } : { ...c.gates, [ev.step]: ev }
      if (ev.step === 1) n.decision = null
      break
    case 'decision': n.decision = ev; n.decisions = [...c.decisions, ev]; break
    case 'agent.audio': n.speaking = ev.speaking; break
    case 'agent.reply': n.transcript = [...c.transcript, { who: 'agent', text: ev.text }]; break
    case 'ticket.created': n.ticket = { id: ev.ticket_id, url: ev.url, tags: ev.tags || [] }; break
    case 'ticket.updated': n.ticket = { ...(c.ticket || {}), id: ev.ticket_id, tags: ev.tags }; break
    case 'undo.started': n.undo = { decisionId: ev.decision_id, finalizeAt: ev.finalize_at, status: 'pending' }; break
    case 'undo.applied': n.undo = { ...c.undo, status: 'undone' }; break
    case 'undo.expired': n.undo = { ...c.undo, status: 'expired' }; break
    case 'refund.issued': n.refund = { ok: true, ...ev }; break
    case 'refund.failed': n.refund = { ok: false, error: ev.error }; break
    case 'call.ended': n.ended = true; n.speaking = false; n.durationS = ev.duration_s; break
    default: return calls
  }
  return { ...calls, [id]: n }
}

// Subscribes to /ws/dashboard. Reconnects on drop. Returns {calls, order, connected, lastEvent}.
export function useLive() {
  const [calls, dispatch] = useReducer(reduce, {})
  const [connected, setConnected] = useState(false)
  const [meta, setMeta] = useState({ profile: null, fault: null })
  const order = useRef([])

  useEffect(() => {
    let ws, timer, stop = false
    const open = () => {
      ws = new WebSocket(wsUrl('/ws/dashboard'))
      ws.onopen = () => setConnected(true)
      ws.onclose = () => { setConnected(false); if (!stop) timer = setTimeout(open, 1500) }
      ws.onmessage = (m) => {
        const ev = JSON.parse(m.data)
        if (ev.type === 'demo.profile') setMeta((x) => ({ ...x, profile: ev.profile }))
        if (ev.type === 'demo.fault_injection') setMeta((x) => ({ ...x, fault: ev.mode }))
        dispatch(ev)
      }
    }
    open()
    return () => { stop = true; clearTimeout(timer); ws && ws.close() }
  }, [])

  const list = Object.values(calls).sort((a, b) => b.startedAt - a.startedAt)
  return { calls: list, byId: calls, connected, meta }
}
