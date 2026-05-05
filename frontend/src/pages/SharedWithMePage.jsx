/**
 * SharedWithMePage — Phase 24C: inbox of files shared with this node by peers.
 *
 * Each row shows who sent it, the filename, size, optional note, and whether
 * it has already been downloaded into the local store. Download uses the
 * existing /download/{hash}?password= flow (encryption is end-to-end so the
 * recipient must know the password the sender encrypted with — same as before).
 *
 * Polls /shares every 5s.
 */

import { useEffect, useMemo, useState } from 'react'
import {
  Inbox, Trash2, ArrowDownToLine, KeyRound, FileText, Check, Send, Route,
  ShieldCheck,
} from 'lucide-react'
import {
  fetchShares, deleteShare, downloadFile, triggerBlobDownload,
  fetchSharePath, ackShareDelivery, fetchThresholdProbe,
} from '../api/client'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'
import RoutePath from '../components/ui/RoutePath'

const POLL_MS = 5000

function formatBytes(bytes) {
  if (!bytes) return '0 B'
  const k = 1024, sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i]
}

function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export default function SharedWithMePage() {
  const [shares, setShares] = useState([])
  const [downloading, setDownloading] = useState({})  // share_id -> bool
  const [passwordPrompt, setPasswordPrompt] = useState(null)
  const [passwordValue, setPasswordValue] = useState('')
  const [error, setError] = useState('')
  // Phase 25A: per-share onion path (after successful download)
  const [paths, setPaths] = useState({})  // share_id -> {self_node_id, self_name, path: [...]}
  const [acked, setAcked] = useState({})  // share_id -> bool
  const [acking, setAcking] = useState({})
  // Phase 25C: per-share threshold probe (file_hash -> probe response)
  const [probes, setProbes] = useState({})

  const loadPathFor = async (share) => {
    try {
      const data = await fetchSharePath(share.id)
      if (data && data.path) setPaths((p) => ({ ...p, [share.id]: data }))
    } catch (e) {
      console.error('fetchSharePath failed:', e)
    }
  }

  const reload = async () => {
    try {
      const data = await fetchShares()
      setShares(data)
    } catch (e) {
      console.error('fetchShares failed:', e)
    }
  }

  // Phase 25C: probe each share's file_hash to learn if it's threshold-encrypted
  // and how many holders are currently reachable.
  const reloadProbes = async (currentShares) => {
    const seen = new Set()
    const tasks = []
    for (const s of currentShares) {
      if (seen.has(s.file_hash)) continue
      seen.add(s.file_hash)
      tasks.push(
        fetchThresholdProbe(s.file_hash)
          .then((p) => [s.file_hash, p])
          .catch(() => [s.file_hash, null]),
      )
    }
    const results = await Promise.all(tasks)
    setProbes((prev) => {
      const next = { ...prev }
      for (const [hash, p] of results) {
        if (p) next[hash] = p
      }
      return next
    })
  }

  useEffect(() => {
    reload()
    const id = setInterval(reload, POLL_MS)
    return () => clearInterval(id)
  }, [])

  // Re-probe whenever shares change, then refresh online-counts every 5s
  useEffect(() => {
    if (shares.length === 0) return
    reloadProbes(shares)
    const id = setInterval(() => reloadProbes(shares), 5000)
    return () => clearInterval(id)
  }, [shares])

  // Group by sender for cleaner display
  const grouped = useMemo(() => {
    const buckets = new Map()
    for (const s of shares) {
      const key = s.from_peer_id
      if (!buckets.has(key)) {
        buckets.set(key, {
          peer_id: s.from_peer_id,
          peer_name: s.from_peer_name || s.from_peer_id.slice(0, 12) + '...',
          online: s.online,
          items: [],
        })
      }
      buckets.get(key).items.push(s)
    }
    return Array.from(buckets.values())
  }, [shares])

  const handleDownloadClick = (share) => {
    const probe = probes[share.file_hash]
    if (probe?.is_threshold) {
      // Threshold-encrypted: the node reconstructs the AES key from M-of-N
      // holders — no password needed. Stream straight into the download flow.
      handleThresholdDownload(share)
      return
    }
    // Regular password-encrypted file
    setPasswordPrompt(share)
    setPasswordValue('')
    setError('')
  }

  const handleThresholdDownload = async (share) => {
    setDownloading((d) => ({ ...d, [share.id]: true }))
    setError('')
    try {
      const { blob, filename } = await downloadFile(share.file_hash, '')
      triggerBlobDownload(blob, filename || share.filename)
      loadPathFor(share)
      reload()
    } catch (err) {
      alert(`Threshold download failed: ${err.message || 'unknown error'}`)
    } finally {
      setDownloading((d) => ({ ...d, [share.id]: false }))
    }
  }

  const handleDownloadConfirm = async () => {
    if (!passwordPrompt) return
    const share = passwordPrompt
    setDownloading((d) => ({ ...d, [share.id]: true }))
    setError('')
    try {
      const { blob, filename } = await downloadFile(share.file_hash, passwordValue)
      triggerBlobDownload(blob, filename || share.filename)
      setPasswordPrompt(null)
      setPasswordValue('')
      // Phase 25A: pull the onion path that was used for this download
      loadPathFor(share)
      reload()
    } catch (err) {
      setError(err.message || 'Download failed')
    } finally {
      setDownloading((d) => ({ ...d, [share.id]: false }))
    }
  }

  const handleAck = async (share) => {
    setAcking((a) => ({ ...a, [share.id]: true }))
    try {
      await ackShareDelivery(share.id)
      setAcked((a) => ({ ...a, [share.id]: true }))
    } catch (err) {
      alert(`Receipt failed: ${err.message}`)
    } finally {
      setAcking((a) => ({ ...a, [share.id]: false }))
    }
  }

  const handleRemove = async (shareId) => {
    if (!confirm('Remove this file from your inbox? (The file itself stays on the network.)')) return
    try {
      await deleteShare(shareId)
      reload()
    } catch (err) {
      alert(`Remove failed: ${err.message}`)
    }
  }

  return (
    <div>
      <Card title="Shared with me" icon={<Inbox size={18} />}>
        {shares.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon"><Inbox size={36} strokeWidth={1.5} /></div>
            <p>Your inbox is empty.</p>
            <span style={{ fontSize: 13 }}>
              Files shared with you by other peers will appear here.
            </span>
          </div>
        ) : (
          <div className="shared-groups">
            {grouped.map((g) => (
              <div key={g.peer_id} className="shared-group">
                <div className="shared-group-header">
                  <span className={`chat-online-dot ${g.online ? 'is-online' : ''}`} />
                  <span className="shared-group-from">From <strong>{g.peer_name}</strong></span>
                  <code className="shared-group-id">{g.peer_id.slice(0, 16)}...</code>
                  <span className="shared-group-count">
                    {g.items.length} file{g.items.length === 1 ? '' : 's'}
                  </span>
                </div>

                <div className="file-list">
                  {g.items.map((s) => {
                    const pathRec = paths[s.id]
                    const probe = probes[s.file_hash]
                    const isThreshold = probe?.is_threshold
                    const decryptable = isThreshold && probe.decryptable_now
                    const downloadDisabled = isThreshold && !decryptable
                    return (
                      <div key={s.id} className="shared-row">
                        <div className="file-item">
                          <div className="file-info">
                            <div className="file-icon"><FileText size={18} /></div>
                            <div>
                              <div className="file-name">
                                {s.filename}
                                {s.downloaded && (
                                  <span className="shared-downloaded-badge">
                                    <Check size={11} /> downloaded
                                  </span>
                                )}
                                {isThreshold && (
                                  <span
                                    className={`threshold-badge ${decryptable ? 'is-ready' : 'is-blocked'}`}
                                    title={decryptable
                                      ? `${probe.online_count} of ${probe.n} holders online — quorum met`
                                      : `Need ${probe.m} holders online; ${probe.online_count} of ${probe.n} reachable`}
                                  >
                                    <ShieldCheck size={11} />
                                    {probe.m}-of-{probe.n} threshold · {probe.online_count}/{probe.n} online
                                  </span>
                                )}
                              </div>
                              <div className="file-meta">
                                {formatBytes(s.size)} · {formatTime(s.sent_at)}
                                {s.note ? <> · "{s.note}"</> : null}
                              </div>
                            </div>
                          </div>
                          <div className="file-actions">
                            <div className="file-hash">{s.file_hash.slice(0, 20)}...</div>
                            <div className="file-buttons">
                              <button
                                className="btn-copy btn-dl"
                                onClick={() => handleDownloadClick(s)}
                                disabled={downloading[s.id] || downloadDisabled}
                                title={
                                  downloadDisabled
                                    ? `Quorum not met — need ${probe.m} of ${probe.n} holders online`
                                    : (isThreshold ? 'Decrypt via threshold quorum + onion route' : 'Download via onion route')
                                }
                              >
                                <ArrowDownToLine size={14} />
                                {downloading[s.id] ? 'Downloading...' : 'Download'}
                              </button>
                              {s.downloaded && !pathRec && (
                                <button
                                  className="btn-copy"
                                  onClick={() => loadPathFor(s)}
                                  title="Show the relay path used"
                                >
                                  <Route size={14} /> Show path
                                </button>
                              )}
                              {pathRec && !acked[s.id] && (
                                <button
                                  className="btn-copy btn-preview"
                                  onClick={() => handleAck(s)}
                                  disabled={acking[s.id]}
                                  title="Tell sender you received it (sends path)"
                                >
                                  <Send size={14} />
                                  {acking[s.id] ? 'Sending...' : 'Send receipt'}
                                </button>
                              )}
                              {acked[s.id] && (
                                <span className="shared-downloaded-badge">
                                  <Check size={11} /> receipt sent
                                </span>
                              )}
                              <button
                                className="btn-copy"
                                onClick={() => handleRemove(s.id)}
                                title="Remove from inbox (file stays on the network)"
                              >
                                <Trash2 size={14} /> Remove
                              </button>
                            </div>
                          </div>
                        </div>
                        {pathRec && pathRec.path && (
                          <div className="shared-path-display">
                            <div className="shared-path-label">
                              <Route size={13} /> Onion path used:
                            </div>
                            <RoutePath
                              hops={pathRec.path}
                              selfNodeId={pathRec.self_node_id}
                              selfName={pathRec.self_name}
                            />
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Password prompt modal — same UX as the dashboard preview prompt */}
      {passwordPrompt && (
        <div className="preview-overlay" onClick={() => setPasswordPrompt(null)}>
          <div className="preview-password-dialog" onClick={(e) => e.stopPropagation()}>
            <h3 style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <KeyRound size={18} /> Decryption password
            </h3>
            <p className="preview-password-filename">{passwordPrompt.filename}</p>
            <input
              type="password"
              className="input-field"
              placeholder="Leave empty if not encrypted"
              value={passwordValue}
              onChange={(e) => setPasswordValue(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleDownloadConfirm()}
              autoFocus
            />
            {error && (
              <div className="alert alert-error" style={{ marginBottom: 12 }}>{error}</div>
            )}
            <div className="preview-password-actions">
              <Button
                variant="primary"
                loading={downloading[passwordPrompt.id]}
                onClick={handleDownloadConfirm}
              >
                Download
              </Button>
              <button
                className="btn"
                style={{ background: 'var(--bg-subtle)', color: 'var(--text-secondary)' }}
                onClick={() => { setPasswordPrompt(null); setError('') }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
