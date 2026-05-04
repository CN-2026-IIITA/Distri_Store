/**
 * Header — Top app bar with branding and connection status.
 */

import { Link } from 'react-router-dom'
import { Activity, Wifi, WifiOff } from 'lucide-react'
import useNetworkStore from '../../store/useNetworkStore'

export default function Header() {
  const status = useNetworkStore((s) => s.status)
  const isConnected = useNetworkStore((s) => s.isConnected)

  return (
    <header className="header">
      <Link to="/" className="header-brand">
        <div className="header-logo">
          <Activity size={19} strokeWidth={2.25} />
        </div>
        <div>
          <div className="header-title">DistriStore</div>
          <div className="header-subtitle">Private distributed file network</div>
        </div>
      </Link>
      <div className="header-status">
        {isConnected ? <Wifi size={14} className="status-icon connected" /> : <WifiOff size={14} className="status-icon disconnected" />}
        <span className="status-dot" />
        {status ? `Node: ${status.name}` : 'Connecting...'}
      </div>
    </header>
  )
}
