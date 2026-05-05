/**
 * SettingsPage — Node configuration and info display.
 */

import { Settings, Server, Shield, Cpu } from 'lucide-react'
import useNetworkStore from '../store/useNetworkStore'
import Card from '../components/ui/Card'

export default function SettingsPage() {
  const status = useNetworkStore((s) => s.status)
 
  return (
    <div>
      <Card title="Node Information" icon={<Server size={20} />}>
        <div className="settings-grid">
          <div className="setting-row">
            <span className="setting-label">Node ID</span>
            <code className="setting-value">{status?.node_id || '—'}</code>
          </div>
          <div className="setting-row">
            <span className="setting-label">Node Name</span>
            <span className="setting-value">{status?.name || '—'}</span>
          </div>
          <div className="setting-row">
            <span className="setting-label">Uptime</span>
            <span className="setting-value">{Math.round(status?.uptime_seconds || 0)}s</span>
          </div>
          <div className="setting-row">
            <span className="setting-label">Connected Peers</span>
            <span className="setting-value">{Object.keys(status?.peers || {}).length}</span>
          </div>
        </div>
      </Card>

      <Card title="Security" icon={<Shield size={20} />}>
        <div className="settings-grid">
          <div className="setting-row">
            <span className="setting-label">Encryption</span>
            <span className="setting-value badge-green">AES-256-GCM</span>
          </div>
          <div className="setting-row">
            <span className="setting-label">Key Derivation</span>
            <span className="setting-value">PBKDF2-HMAC-SHA256 (100K iterations)</span>
          </div>
          <div className="setting-row">
            <span className="setting-label">Integrity</span>
            <span className="setting-value badge-green">Merkle Tree + GCM Auth Tag</span>
          </div>
          {status?.swarm_auth_active && (
            <div className="setting-row">
              <span className="setting-label">Network Trust</span>
              <span className="setting-value badge-green">Swarm PSK: Active</span>
            </div>
          )}
          <div className="setting-row">
            <span className="setting-label">Chunk Size</span>
            <span className="setting-value">256 KB</span>
          </div>
        </div>
      </Card>

      <Card title="Performance" icon={<Cpu size={20} />}>
        <div className="settings-grid">
          <div className="setting-row">
            <span className="setting-label">Pipeline</span>
            <span className="setting-value badge-blue">Streaming O(N)</span>
          </div>
          <div className="setting-row">
            <span className="setting-label">Crypto Engine</span>
            <span className="setting-value">ProcessPoolExecutor (GIL bypass)</span>
          </div>
          <div className="setting-row">
            <span className="setting-label">Memory Model</span>
            <span className="setting-value badge-blue">O(1) — FileResponse streaming</span>
          </div>
          <div className="setting-row">
            <span className="setting-label">100MB Throughput</span>
            <span className="setting-value badge-green">0.78s (128 MB/s)</span>
          </div>
        </div>
      </Card>
    </div>
  )
}
