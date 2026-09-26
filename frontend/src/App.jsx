import { useEffect, useState } from 'react'
import { api } from './api.js'
import { useLive } from './useLive.js'
import LiveDemo from './pages/LiveDemo.jsx'
import Sop from './pages/Sop.jsx'
import AuditLog from './pages/AuditLog.jsx'
import Savings from './pages/Savings.jsx'
import { AuditIcon, LiveIcon, RulesIcon, SavingsIcon, ShieldIcon } from './components/Icons.jsx'

const TABS = [['live', 'Live demo', LiveIcon], ['sop', 'SOP rules', RulesIcon], ['audit', 'Audit log', AuditIcon], ['savings', 'Savings', SavingsIcon]]

export default function App() {
  const [tab, setTab] = useState('live')
  const [info, setInfo] = useState(null)
  const live = useLive()

  useEffect(() => { api('/api/demo/info').then(setInfo).catch(() => {}) }, [live.connected])
  const profile = live.meta.profile ?? info?.next_profile
  const fault = live.meta.fault ?? info?.fault_injection

  return (
    <div className="shell">
      <nav className="rail" aria-label="GuardRail sections">
        <div className="mark" title="GuardRail"><ShieldIcon /></div>
        {TABS.map(([k, label, Icon]) => (
          <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)} aria-current={tab === k ? 'page' : undefined}>
            <Icon />{label}
          </button>
        ))}
      </nav>
      <div className="main">
        <header className="top">
          <div className="brand">GuardRail for Freshdesk<small>Built for Freshworks</small></div>
          <div className="page-title">{TABS.find(([k]) => k === tab)[1]}</div>
          <div className="spacer" />
          <div className="chips">
            {info?.phone_number && <span className="lbl">📞 {info.phone_number}</span>}
            {info && <span className="lbl blue">refunds: {info.payments_provider === 'simulated' ? 'Simulated' : 'Dodo (test mode)'}</span>}
            {info && <span className="lbl blue">tickets: {info.helpdesk}</span>}
            <span className={'lbl ' + (live.connected ? 'green' : 'grey')}><span className={'dot' + (live.connected ? ' on' : '')} />{live.connected ? 'live' : 'reconnecting…'}</span>
          </div>
        </header>
        <main className="content">
          {tab === 'live' && <LiveDemo live={live} info={info} profile={profile} fault={fault} />}
          {tab === 'sop' && <Sop />}
          {tab === 'audit' && <AuditLog />}
          {tab === 'savings' && <Savings />}
        </main>
      </div>
    </div>
  )
}
