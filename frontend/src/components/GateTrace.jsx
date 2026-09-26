const GATES = [
  [1, 'Transcript & understanding'],
  [2, 'Negation & contradiction'],
  [3, 'SOP rules'],
  [4, 'Risk & abuse'],
  [5, 'Decision'],
]

// Accepts live `gate` events keyed by step, or a stored trace_json array.
export default function GateTrace({ steps }) {
  const by = Array.isArray(steps) ? Object.fromEntries(steps.map((s) => [s.step, s])) : steps || {}
  return (
    <div className="steps">
      {GATES.map(([n, title]) => {
        const s = by[n]
        return (
          <div key={n} className={'step ' + (s ? s.status : 'waiting')}>
            <div className="num">{n}</div>
            <div>
              <div className="t">{title}</div>
              <div className="d">{s ? s.detail : 'waiting…'}</div>
            </div>
            {s && <div className={'st ' + s.status}>{s.status}</div>}
          </div>
        )
      })}
    </div>
  )
}
