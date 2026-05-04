/**
 * PeerTable — Sortable table of connected peers with health scores and latency.
 * Reads from Zustand store — no props needed.
 */

import { useState } from 'react'
import { Users, ArrowUpDown, Wifi, Heart } from 'lucide-react'
import useNetworkStore from '../../store/useNetworkStore'
import Card from '../ui/Card'

export default function PeerTable() {
  const status = useNetworkStore((s) => s.status)
  const [sortKey, setSortKey] = useState('name')
  const [sortAsc, setSortAsc] = useState(true)

  const peers = status?.peers || {}
  const peerList = Object.entries(peers).map(([id, info]) => ({
    id,
    name: info.name || id.slice(0, 12),
    host: info.host || 'unknown',
    port: info.port || 0,
    health_score: info.health_score || 0,
  }))

  const handleSort = (key) => {
    if (sortKey === key) {
      setSortAsc(!sortAsc)
    } else {
      setSortKey(key)
      setSortAsc(true)
    }
  }

  const sorted = [...peerList].sort((a, b) => {
    const va = a[sortKey] ?? ''
    const vb = b[sortKey] ?? ''
    const cmp = typeof va === 'number' ? va - vb : String(va).localeCompare(String(vb))
    return sortAsc ? cmp : -cmp
  })

  const columns = [
    { key: 'name', label: 'Node', icon: <Users size={13} /> },
    { key: 'host', label: 'Host', icon: <Wifi size={13} /> },
    { key: 'health_score', label: 'Health', icon: <Heart size={13} /> },
  ]

  return (
    <Card title="Connected Peers" icon={<Users size={18} />} noPad>
      {peerList.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon"><Users size={32} strokeWidth={1.5} /></div>
          <p>No peers connected yet</p>
        </div>
      ) : (
        <div className="peer-table-wrap">
          <table className="peer-table">
            <thead>
              <tr>
                {columns.map((col) => (
                  <th key={col.key} onClick={() => handleSort(col.key)}>
                    <span className="th-inner">
                      {col.icon} {col.label}
                      <ArrowUpDown size={11} style={{ opacity: sortKey === col.key ? 1 : 0.3 }} />
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((peer) => (
                <tr key={peer.id}>
                  <td>
                    <span className="peer-name">{peer.name}</span>
                    <span className="peer-id">{peer.id.slice(0, 12)}...</span>
                  </td>
                  <td><code>{peer.host}:{peer.port}</code></td>
                  <td>
                    <span className={`health-badge ${peer.health_score > 500 ? 'health-good' : peer.health_score > 200 ? 'health-ok' : 'health-low'}`}>
                      {Math.round(peer.health_score)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
