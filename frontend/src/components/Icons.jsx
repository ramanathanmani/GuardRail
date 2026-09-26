// Simple line icons for the nav rail (generic shapes; no Freshworks marks).
const Svg = ({ children }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {children}
  </svg>
)

export const ShieldIcon = () => (
  <Svg><path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6l7-3z" /><path d="M9 12l2 2 4-4" /></Svg>
)
export const LiveIcon = () => (
  <Svg><path d="M4 14v-2a8 8 0 0116 0v2" /><rect x="3" y="14" width="4" height="6" rx="1.5" /><rect x="17" y="14" width="4" height="6" rx="1.5" /></Svg>
)
export const RulesIcon = () => (
  <Svg><rect x="5" y="4" width="14" height="17" rx="2" /><path d="M9 4V3h6v1" /><path d="M9 10h6M9 14h6M9 18h3" /></Svg>
)
export const AuditIcon = () => (
  <Svg><path d="M3 12a9 9 0 103-6.7" /><path d="M3 4v4h4" /><path d="M12 8v4l3 2" /></Svg>
)
export const SavingsIcon = () => (
  <Svg><path d="M4 20h16" /><path d="M7 16v-5M12 16V7M17 16v-8" /></Svg>
)
export const TicketIcon = () => (
  <Svg><path d="M4 7a2 2 0 012-2h12a2 2 0 012 2v2a2 2 0 000 4v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2a2 2 0 000-4V7z" /></Svg>
)
