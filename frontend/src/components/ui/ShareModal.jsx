/**
 * ShareModal — Phase 24C: Send a list of file hashes to a peer's inbox.
 *
 * Recipient is picked from the alive-peers list (dropdown) or pasted as
 * a raw peer ID. The recipient sees the share in their /shares inbox and
 * can selectively download whichever files they want via the existing
 * /download/{hash} flow.
 */

import { useEffect, useState } from 'react'
import { Send, X, Users, MessageSquarePlus } from 'lucide-react'
import { Link } from 'react-router-dom'
import { shareFiles, fetchChats } from '../../api/client'
import useNetworkStore from '../../store/useNetworkStore'
import Button from './Button'

function formatBytes(bytes) {
  if (!bytes) return '0 B'
  const k = 1024, sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i]
}

export default function ShareModal({ files, onClose, onShared }) {
  const status = useNetworkStore((s) => s.status)
  const [recipient, setRecipient] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [chatPartners, setChatPartners] = useState([])  // [{peer_id, peer_name, online}]
  const [loadingChats, setLoadingChats] = useState(true)

  // Pull the list of accepted chat threads — these are the only peers
  // we are allowed to share files with (Phase 24C+ consent gate).
  useEffect(() => {
    let cancelled = false
    fetchChats()
      .then((data) => {
        if (cancelled) return
        const accepted = (data.chats || []).filter((c) => c.status === 'accepted')
        const alive = status?.peers || {}
        setChatPartners(accepted.map((c) => ({
          peer_id: c.peer_id,
          peer_name: c.peer_name || alive[c.peer_id]?.name || c.peer_id.slice(0, 12),
          online: c.online,
        })))
      })
      .catch((e) => console.error('fetchChats failed:', e))
      .finally(() => { if (!cancelled) setLoadingChats(false) })
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Default: pick the first online chat partner once the list arrives
  useEffect(() => {
    if (!recipient && chatPartners.length) {
      const firstOnline = chatPartners.find((p) => p.online) || chatPartners[0]
      setRecipient(firstOnline.peer_id)
    }
  }, [chatPartners, recipient])

  if (!files || files.length === 0) return null

  const handleSend = async () => {
    setError('')
    if (!recipient.trim()) { setError('Pick a peer first'); return }
    setBusy(true)
    try {
      await shareFiles(
        recipient.trim(),
        files.map((f) => f.file_hash),
        note.trim(),
      )
      onShared?.()
      onClose?.()
    } catch (err) {
      setError(err.message || 'Share failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="preview-overlay" onClick={onClose}>
      <div className="share-modal" onClick={(e) => e.stopPropagation()}>
        <div className="share-modal-header">
          <h3>Share {files.length} file{files.length === 1 ? '' : 's'}</h3>
          <button className="chat-close-btn" onClick={onClose} title="Close">
            <X size={18} />
          </button>
        </div>

        <div className="share-modal-body">
          {/* File list preview */}
          <div className="share-file-list">
            {files.map((f) => (
              <div key={f.file_hash} className="share-file-item">
                <div className="share-file-name">{f.filename}</div>
                <div className="share-file-meta">
                  {formatBytes(f.size)} · <code>{f.file_hash.slice(0, 16)}...</code>
                </div>
              </div>
            ))}
          </div>

          {/* Recipient picker — restricted to peers with an accepted chat */}
          <div className="input-group">
            <label><Users size={14} /> Send to chat partner</label>
            {loadingChats ? (
              <div className="alert" style={{ padding: 10 }}>Loading chats...</div>
            ) : chatPartners.length === 0 ? (
              <div className="alert">
                <div style={{ flex: 1 }}>
                  You can only share files with peers you have an accepted chat with.
                  <Link
                    to="/chats"
                    onClick={onClose}
                    style={{ display: 'inline-flex', alignItems: 'center', gap: 4,
                             marginLeft: 8, color: 'var(--accent)', fontWeight: 600 }}
                  >
                    <MessageSquarePlus size={14} /> Start a chat
                  </Link>
                </div>
              </div>
            ) : (
              <select
                className="input-field"
                value={recipient}
                onChange={(e) => setRecipient(e.target.value)}
              >
                {chatPartners.map((p) => (
                  <option key={p.peer_id} value={p.peer_id}>
                    {p.peer_name} {p.online ? '· online' : '· offline'} ({p.peer_id.slice(0, 12)}...)
                  </option>
                ))}
              </select>
            )}
          </div>

          {/* Optional note */}
          <div className="input-group">
            <label>Note (optional)</label>
            <input
              className="input-field"
              placeholder="A message that arrives with the files..."
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={280}
            />
          </div>

          {error && <div className="alert alert-error">{error}</div>}
        </div>

        <div className="share-modal-actions">
          <button
            className="btn"
            style={{ background: 'var(--bg-subtle)', color: 'var(--text-secondary)' }}
            onClick={onClose}
            disabled={busy}
          >
            Cancel
          </button>
          <Button
            variant="primary"
            loading={busy}
            disabled={!recipient.trim() || files.length === 0 || chatPartners.length === 0}
            onClick={handleSend}
            icon={<Send size={14} />}
          >
            Share {files.length} file{files.length === 1 ? '' : 's'}
          </Button>
        </div>
      </div>
    </div>
  )
}
