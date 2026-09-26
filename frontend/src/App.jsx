import { useEffect, useState } from 'react'
import { api } from './api.js'
import { useLive } from './useLive.js'
import LiveDemo from './pages/LiveDemo.jsx'
import Sop from './pages/Sop.jsx'
import AuditLog from './pages/AuditLog.jsx'
import Savings from './pages/Savings.jsx'

const TABS = [['live', 'Live demo'], ['sop', 'SOP rules'], ['audit', 'Audit log'], ['savings', 'Savings']]

export default function App() {
  const [tab, setTab] = useState('live')
  const [info, setInfo] = useState(null)
  const live = useLive()

  useEffect(() => { api('/api/demo/info').then(setInfo).catch(() => {}) }, [live.connected])
  const profile = live.meta.profile ?? info?.next_profile
  const fault = live.meta.fault ?? info?.fault_injection

  return (
    <div className="app">
      <header className="top">
        <div className="brand">Guard<span>Rail</span></div>
        <nav className="tabs">
          {TABS.map(([k, label]) => (
            <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{label}</button>
          ))}
        </nav>
        <div className="spacer" />
        {info?.phone_number && <span className="pill">📞 {info.phone_number}</span>}
        {info && <span className="pill">refunds: {info.payments_provider === 'simulated' ? 'Simulated' : 'Dodo (test mode)'}</span>}
        {info && <span className="pill">tickets: {info.helpdesk}</span>}
        <span className="pill"><span className={'dot' + (live.connected ? ' on' : '')} />{live.connected ? 'live' : 'reconnecting…'}</span>
      </header>
      {tab === 'live' && <LiveDemo live={live} info={info} profile={profile} fault={fault} />}
      {tab === 'sop' && <Sop />}
      {tab === 'audit' && <AuditLog />}
      {tab === 'savings' && <Savings />}
    </div>
  )
}
