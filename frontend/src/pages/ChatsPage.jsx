/**
 * ChatsPage — Phase 24A: 1:1 invite-based chat threads.
 *
 * Three sections:
 *   1. New chat: paste a peer ID and send an invite
 *   2. Pending invites (incoming): Accept / Reject buttons
 *   3. Active chats: pick a thread, see messages, send replies
 *
 * Polls /chats every 3s to pick up new invites and incoming messages.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  MessageSquare, Send, Check, X, UserPlus, Trash2,
  Inbox, Loader2, Circle,
} from 'lucide-react'
import {
  fetchChats, inviteChat, acceptChat, rejectChat,
  deleteChat, sendChatMessage, fetchChatMessages,
} from '../api/client'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'

const POLL_MS = 3000

function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  return sameDay
    ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export default function ChatsPage() {
  const [chats, setChats] = useState([])
  const [selfId, setSelfId] = useState('')
  const [selectedPeer, setSelectedPeer] = useState(null)
  const [messages, setMessages] = useState([])
  const [draft, setDraft] = useState('')
  const [invitePeerId, setInvitePeerId] = useState('')
  const [inviteError, setInviteError] = useState('')
  const [sending, setSending] = useState(false)
  const [busy, setBusy] = useState({})  // peer_id -> bool for Accept/Reject buttons
  const messagesEndRef = useRef(null)

  // Poll the chat list
  const reloadChats = async () => {
    try {
      const data = await fetchChats()
      setChats(data.chats || [])
      setSelfId(data.self_node_id || '')
    } catch (e) {
      console.error('fetchChats failed:', e)
    }
  }

  useEffect(() => {
    reloadChats()
    const id = setInterval(reloadChats, POLL_MS)
    return () => clearInterval(id)
  }, [])

  // Poll the active thread's messages
  useEffect(() => {
    if (!selectedPeer) { setMessages([]); return }
    let cancelled = false
    const reloadMsgs = async () => {
      try {
        const data = await fetchChatMessages(selectedPeer)
        if (!cancelled) setMessages(data.messages || [])
      } catch (e) {
        if (!cancelled) console.error('fetchChatMessages failed:', e)
      }
    }
    reloadMsgs()
    const id = setInterval(reloadMsgs, POLL_MS)
    return () => { cancelled = true; clearInterval(id) }
  }, [selectedPeer])

  // Auto-scroll thread to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Group chats by status for the sidebar
  const grouped = useMemo(() => {
    const incoming = chats.filter((c) => c.status === 'incoming_pending')
    const outgoing = chats.filter((c) => c.status === 'outgoing_pending')
    const active = chats.filter((c) => c.status === 'accepted')
    return { incoming, outgoing, active }
  }, [chats])

  const selectedThread = chats.find((c) => c.peer_id === selectedPeer) || null

  // ── Actions ──────────────────────────────────────────────

  const handleInvite = async (e) => {
    e?.preventDefault()
    setInviteError('')
    const id = invitePeerId.trim()
    if (!id) return
    if (id === selfId) { setInviteError('That is your own node ID.'); return }
    try {
      await inviteChat(id)
      setInvitePeerId('')
      reloadChats()
    } catch (err) {
      setInviteError(err.message || 'Invite failed')
    }
  }

  const handleAccept = async (peerId) => {
    setBusy((b) => ({ ...b, [peerId]: true }))
    try {
      await acceptChat(peerId)
      setSelectedPeer(peerId)
      reloadChats()
    } catch (err) {
      alert(`Accept failed: ${err.message}`)
    } finally {
      setBusy((b) => ({ ...b, [peerId]: false }))
    }
  }

  const handleReject = async (peerId) => {
    setBusy((b) => ({ ...b, [peerId]: true }))
    try {
      await rejectChat(peerId)
      if (selectedPeer === peerId) setSelectedPeer(null)
      reloadChats()
    } catch (err) {
      alert(`Reject failed: ${err.message}`)
    } finally {
      setBusy((b) => ({ ...b, [peerId]: false }))
    }
  }

  const handleDelete = async (peerId) => {
    if (!confirm('Delete this chat thread and all its messages?')) return
    try {
      await deleteChat(peerId)
      if (selectedPeer === peerId) setSelectedPeer(null)
      reloadChats()
    } catch (err) {
      alert(`Delete failed: ${err.message}`)
    }
  }

  const handleSend = async (e) => {
    e?.preventDefault()
    if (!selectedPeer || !draft.trim() || sending) return
    setSending(true)
    try {
      await sendChatMessage(selectedPeer, draft.trim())
      setDraft('')
      // Refresh immediately so the sent message appears without waiting for poll
      const data = await fetchChatMessages(selectedPeer)
      setMessages(data.messages || [])
    } catch (err) {
      alert(`Send failed: ${err.message}`)
    } finally {
      setSending(false)
    }
  }

  // ── Render ───────────────────────────────────────────────

  return (
    <div className="chats-layout">
      {/* Left column: thread list + invite form */}
      <div className="chats-sidebar">
        <Card title="New chat" icon={<UserPlus size={18} />}>
          <form className="form-section" onSubmit={handleInvite}>
            <div className="input-group">
              <label>Invite by peer ID</label>
              <input
                className="input-field"
                placeholder="paste a peer node ID..."
                value={invitePeerId}
                onChange={(e) => setInvitePeerId(e.target.value)}
              />
            </div>
            <Button variant="primary" disabled={!invitePeerId.trim()}>
              Send invite
            </Button>
            {inviteError && <div className="alert alert-error">{inviteError}</div>}
          </form>
        </Card>

        {grouped.incoming.length > 0 && (
          <Card title="Pending invites" icon={<Inbox size={18} />}>
            <div className="chats-list">
              {grouped.incoming.map((c) => (
                <ThreadRow
                  key={c.peer_id}
                  chat={c}
                  selected={selectedPeer === c.peer_id}
                  onSelect={() => setSelectedPeer(c.peer_id)}
                  actions={
                    <div className="thread-row-actions">
                      <button
                        className="btn-copy btn-dl"
                        disabled={busy[c.peer_id]}
                        onClick={(e) => { e.stopPropagation(); handleAccept(c.peer_id) }}
                      >
                        <Check size={14} /> Accept
                      </button>
                      <button
                        className="btn-copy"
                        disabled={busy[c.peer_id]}
                        onClick={(e) => { e.stopPropagation(); handleReject(c.peer_id) }}
                      >
                        <X size={14} /> Reject
                      </button>
                    </div>
                  }
                />
              ))}
            </div>
          </Card>
        )}

        <Card title="Chats" icon={<MessageSquare size={18} />}>
          {grouped.active.length === 0 && grouped.outgoing.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon"><MessageSquare size={32} strokeWidth={1.5} /></div>
              <p>No chats yet. Send an invite to start one.</p>
            </div>
          ) : (
            <div className="chats-list">
              {grouped.active.map((c) => (
                <ThreadRow
                  key={c.peer_id}
                  chat={c}
                  selected={selectedPeer === c.peer_id}
                  onSelect={() => setSelectedPeer(c.peer_id)}
                />
              ))}
              {grouped.outgoing.map((c) => (
                <ThreadRow
                  key={c.peer_id}
                  chat={c}
                  selected={selectedPeer === c.peer_id}
                  onSelect={() => setSelectedPeer(c.peer_id)}
                  pendingLabel="waiting for accept..."
                />
              ))}
            </div>
          )}
        </Card>
      </div>

      {/* Right column: open thread */}
      <div className="chats-pane">
        {selectedThread ? (
          <Card
            title={
              <div className="chat-thread-title">
                <span>{selectedThread.peer_name || selectedThread.peer_id.slice(0, 12) + '...'}</span>
                <span className={`chat-online-dot ${selectedThread.online ? 'is-online' : ''}`} />
                <span className="chat-online-label">
                  {selectedThread.online ? 'online' : 'offline'}
                </span>
              </div>
            }
            icon={<MessageSquare size={18} />}
          >
            <div className="chat-thread-meta">
              <code>{selectedThread.peer_id}</code>
              <button
                className="btn-sm"
                onClick={() => handleDelete(selectedThread.peer_id)}
                title="Delete this thread"
              >
                <Trash2 size={12} /> Delete
              </button>
            </div>

            {selectedThread.status === 'outgoing_pending' && (
              <div className="alert">
                <Loader2 size={14} className="spin-slow" />
                Waiting for {selectedThread.peer_name || 'peer'} to accept your invite.
              </div>
            )}

            {selectedThread.status === 'accepted' && (
              <>
                <div className="chat-thread-log">
                  {messages.length === 0 ? (
                    <div className="chat-empty">
                      <p>No messages yet.</p>
                      <span>Say hi!</span>
                    </div>
                  ) : (
                    messages.map((m) => (
                      <div
                        key={m.id}
                        className={`chat-bubble ${m.from_self ? 'chat-bubble-self' : 'chat-bubble-peer'}`}
                      >
                        <div className="chat-text">{m.body}</div>
                        <div className="chat-time">{formatTime(m.sent_at)}</div>
                      </div>
                    ))
                  )}
                  <div ref={messagesEndRef} />
                </div>

                <form className="chat-thread-input" onSubmit={handleSend}>
                  <input
                    className="chat-input"
                    placeholder={selectedThread.online ? 'Type a message...' : 'Peer is offline — message may not deliver'}
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    disabled={sending}
                  />
                  <button
                    className="chat-send-btn"
                    type="submit"
                    disabled={!draft.trim() || sending}
                    title="Send"
                  >
                    <Send size={18} />
                  </button>
                </form>
              </>
            )}
          </Card>
        ) : (
          <Card>
            <div className="empty-state">
              <div className="empty-state-icon"><MessageSquare size={36} strokeWidth={1.5} /></div>
              <p>Pick a chat from the left, or send a new invite.</p>
            </div>
          </Card>
        )}
      </div>
    </div>
  )
}

// ── Thread row in the left list ───────────────────────────

function ThreadRow({ chat, selected, onSelect, actions, pendingLabel }) {
  return (
    <div
      className={`thread-row ${selected ? 'thread-row-selected' : ''}`}
      onClick={onSelect}
    >
      <div className="thread-row-main">
        <div className="thread-row-name">
          <span className={`chat-online-dot ${chat.online ? 'is-online' : ''}`} />
          {chat.peer_name || chat.peer_id.slice(0, 12) + '...'}
        </div>
        <div className="thread-row-sub">
          {pendingLabel
            ? <em>{pendingLabel}</em>
            : chat.last_message
              ? `${chat.last_message_from_self ? 'You: ' : ''}${chat.last_message.slice(0, 60)}`
              : <em>no messages yet</em>}
        </div>
      </div>
      {actions || (chat.last_message_at && (
        <div className="thread-row-time">{formatTime(chat.last_message_at)}</div>
      ))}
    </div>
  )
}
