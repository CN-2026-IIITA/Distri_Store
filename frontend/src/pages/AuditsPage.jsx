/**
 * AuditsPage — Phase 25B: Proof-of-Storage scoreboard.
 *
 * Two sections:
 *   1. Reputation table — per-peer pass / fail / pass-rate, last audit time, 
 *      plus an "Audit now" button to manually challenge a specific peer.
 *   2. Audit log — most-recent challenges in chronological order, showing
 *      the chunk, nonce, expected vs received proof, and the result.
 *
 * Polls every 7s so the background auditor's results show up live.
 */

import { useEffect, useMemo, useState } from 'react'
import {
  ShieldCheck, AlertTriangle, Activity, RefreshCw, Hash, Dice5,
} from 'lucide-react'
import {
  fetchAuditReputation, fetchAuditLog, runAuditAgainst, runAuditRandom,
} from '../api/client'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'

const POLL_MS = 7000

function ago(ts) {
  if (!ts) return 'never'
  const seconds = Math.floor(Date.now() / 1000 - ts)
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function ResultBadge({ result }) {
  const map = {
    pass:  { color: '#047857', bg: 'rgba(16,185,129,0.12)',  label: 'pass'  },
    fail:  { color: '#b91c1c', bg: 'rgba(239,68,68,0.12)',   label: 'fail'  },
    error: { color: '#b45309', bg: 'rgba(245,158,11,0.12)',  label: 'error' },
  }
  const m = map[result] || map.error
  return (
    <span
      className="health-badge"
      style={{ background: m.bg, color: m.color }}
    >
      {m.label}
    </span>
  )
}

export default function AuditsPage() {
  const [reputation, setReputation] = useState([])
  const [log, setLog] = useState([])
  const [busy, setBusy] = useState({})  // peer_id -> bool
  const [randomBusy, setRandomBusy] = useState(false)

  const reload = async () => {
    try {
      const [rep, lg] = await Promise.all([fetchAuditReputation(), fetchAuditLog(50)])
      setReputation(rep)
      setLog(lg)
    } catch (e) { console.error('audit reload failed:', e) }
  }

  useEffect(() => {
    reload()
    const id = setInterval(reload, POLL_MS)
    return () => clearInterval(id)
  }, [])

  const handleAuditOne = async (peerId) => {
    setBusy((b) => ({ ...b, [peerId]: true }))
    try { await runAuditAgainst(peerId); await reload() }
    catch (err) { alert(`Audit failed: ${err.message}`) }
    finally { setBusy((b) => ({ ...b, [peerId]: false })) }
  }

  const handleAuditRandom = async () => {
    setRandomBusy(true)
    try { await runAuditRandom(); await reload() }
    catch (err) { alert(`Audit failed: ${err.message}`) }
    finally { setRandomBusy(false) }
  }

  const totals = useMemo(() => {
    const pass = reputation.reduce((s, r) => s + (r.passed || 0), 0)
    const fail = reputation.reduce((s, r) => s + (r.failed || 0), 0)
    const errs = reputation.reduce((s, r) => s + (r.errors || 0), 0)
    const total = pass + fail + errs
    return { pass, fail, errs, total, rate: total ? Math.round((pass / total) * 100) : 0 }
  }, [reputation])

  return (
    <div>
      <Card title="Proof-of-storage" icon={<ShieldCheck size={18} />}>
        <p className="audits-intro">
          Each audit asks a peer to prove they still hold a chunk we know they
          were assigned: we send a random nonce, they reply with{' '}
          <code>SHA-256(chunk || nonce)</code>, we verify it against our own
          copy. Peers that deleted chunks can&rsquo;t fake the proof — they get
          marked as fails.
        </p>

        <div className="audits-summary">
          <div className="audit-stat">
            <div className="audit-stat-value" style={{ color: '#047857' }}>{totals.pass}</div>
            <div className="audit-stat-label">passed</div>
          </div>
          <div className="audit-stat">
            <div className="audit-stat-value" style={{ color: '#b91c1c' }}>{totals.fail}</div>
            <div className="audit-stat-label">failed</div>
          </div>
          <div className="audit-stat">
            <div className="audit-stat-value" style={{ color: '#b45309' }}>{totals.errs}</div>
            <div className="audit-stat-label">errors</div>
          </div>
          <div className="audit-stat">
            <div className="audit-stat-value">{totals.rate}%</div>
            <div className="audit-stat-label">global pass-rate</div>
          </div>
          <div className="audit-stat-spacer" />
          <Button
            variant="primary"
            loading={randomBusy}
            onClick={handleAuditRandom}
            icon={<Dice5 size={14} />}
          >
            Audit a random peer
          </Button>
        </div>
      </Card>

      <Card title="Per-peer reputation" icon={<Activity size={18} />} noPad>
        {reputation.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon"><ShieldCheck size={32} strokeWidth={1.5} /></div>
            <p>No audits run yet. The background auditor runs every 30s.</p>
          </div>
        ) : (
          <div className="peer-table-wrap">
            <table className="peer-table">
              <thead>
                <tr>
                  <th>Peer</th>
                  <th>Pass</th>
                  <th>Fail</th>
                  <th>Errors</th>
                  <th>Pass-rate</th>
                  <th>Last audit</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {reputation.map((r) => {
                  const pct = Math.round((r.pass_rate || 0) * 100)
                  const tone = pct >= 80 ? 'health-good' : pct >= 40 ? 'health-ok' : 'health-low'
                  return (
                    <tr key={r.peer_id}>
                      <td>
                        <span className="peer-name">{r.peer_name || r.peer_id.slice(0, 12) + '...'}</span>
                        <span className="peer-id">{r.peer_id.slice(0, 16)}...</span>
                      </td>
                      <td><strong style={{ color: '#047857' }}>{r.passed}</strong></td>
                      <td><strong style={{ color: r.failed ? '#b91c1c' : 'var(--text-muted)' }}>{r.failed}</strong></td>
                      <td>{r.errors || 0}</td>
                      <td>
                        <span className={`health-badge ${tone}`}>{pct}%</span>
                      </td>
                      <td>
                        <span style={{ color: r.online ? 'var(--text-secondary)' : 'var(--text-muted)' }}>
                          {ago(r.last_audit_at)}
                        </span>
                      </td>
                      <td>
                        <button
                          className="btn-sm"
                          onClick={() => handleAuditOne(r.peer_id)}
                          disabled={busy[r.peer_id] || !r.online}
                          title={r.online ? 'Run an audit against this peer now' : 'Peer is offline'}
                        >
                          <RefreshCw size={12} />
                          {busy[r.peer_id] ? 'Auditing...' : 'Audit now'}
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Recent audits" icon={<Hash size={18} />} noPad>
        {log.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon"><AlertTriangle size={32} strokeWidth={1.5} /></div>
            <p>No audit log entries yet.</p>
          </div>
        ) : (
          <div className="audit-log">
            {log.map((entry) => (
              <div key={entry.id} className="audit-log-row">
                <div className="audit-log-meta">
                  <ResultBadge result={entry.result} />
                  <span className="audit-log-time">{formatTime(entry.challenged_at)}</span>
                  <span className="audit-log-peer">
                    {entry.peer_name || entry.peer_id.slice(0, 10) + '...'}
                  </span>
                </div>
                <div className="audit-log-detail">
                  <span title="Chunk being challenged">
                    chunk <code>{entry.chunk_hash.slice(0, 14)}...</code>
                  </span>
                  <span title="Random nonce we sent">
                    nonce <code>{entry.nonce.slice(0, 12)}...</code>
                  </span>
                  {entry.error && (
                    <span className="audit-log-error">{entry.error}</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}
