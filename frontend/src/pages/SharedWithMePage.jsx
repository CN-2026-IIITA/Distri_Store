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
  Inbox, Trash2, ArrowDownToLine, KeyRound, FileText, Check,
} from 'lucide-react'
import {
  fetchShares, deleteShare, downloadFile, triggerBlobDownload,
} from '../api/client'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'

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

  const reload = async () => {
    try {
      const data = await fetchShares()
      setShares(data)
    } catch (e) {
      console.error('fetchShares failed:', e)
    }
  }

  useEffect(() => {
    reload()
    const id = setInterval(reload, POLL_MS)
    return () => clearInterval(id)
  }, [])

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
    // Always prompt for password since files in this project are encrypted by default
    setPasswordPrompt(share)
    setPasswordValue('')
    setError('')
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
      reload()
    } catch (err) {
      setError(err.message || 'Download failed')
    } finally {
      setDownloading((d) => ({ ...d, [share.id]: false }))
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
                  {g.items.map((s) => (
                    <div key={s.id} className="file-item">
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
                            disabled={downloading[s.id]}
                            title="Download this file"
                          >
                            <ArrowDownToLine size={14} />
                            {downloading[s.id] ? 'Downloading...' : 'Download'}
                          </button>
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
                  ))}
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
