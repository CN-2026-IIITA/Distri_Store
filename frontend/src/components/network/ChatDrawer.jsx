/**
 * ChatDrawer — P2P Swarm Chat (Phase 19)
 *
 * Renders a slide-out chat panel anchored to the bottom-right.
 * Key feature: detects SHA-256 hashes in messages and renders them
 * as clickable links that navigate to the download page.
 */

import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { MessageCircle, Send, X, Wifi, WifiOff } from 'lucide-react'
import useNetworkStore from '../../store/useNetworkStore'

// Regex to detect 64-char hex strings (SHA-256 hashes)
const HASH_REGEX = /\b([a-f0-9]{64})\b/gi

function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

/** Renders message text with clickable hash links */
function ChatText({ text }) {
  const navigate = useNavigate()
  const parts = []
  let lastIndex = 0
  let match

  // Reset regex state
  HASH_REGEX.lastIndex = 0

  while ((match = HASH_REGEX.exec(text)) !== null) {
    // Add text before the hash
    if (match.index > lastIndex) {
      parts.push(<span key={lastIndex}>{text.slice(lastIndex, match.index)}</span>)
    }
    // Add clickable hash buttons (Preview + Download)
    const hash = match[1]
    parts.push(
      <span key={match.index} className="chat-hash-group">
        <button
          className="chat-hash-link"
          onClick={() => navigate(`/download?hash=${hash}`)}
          title={`Download: ${hash}`}
        >
          📦 {hash.slice(0, 12)}...{hash.slice(-8)}
        </button>
        <button
          className="chat-hash-link chat-hash-preview"
          onClick={() => navigate(`/download?hash=${hash}&preview=1`)}
          title="Preview this file"
        >
          👁️
        </button>
      </span>
    )
    lastIndex = match.index + match[0].length
  }

  // Add remaining text
  if (lastIndex < text.length) {
    parts.push(<span key={lastIndex}>{text.slice(lastIndex)}</span>)
  }

  return <>{parts}</>
}

export default function ChatDrawer({ visible = false }) {
  if (!visible) return null;

  const [isOpen, setIsOpen] = useState(false)
  const [input, setInput] = useState('')
  const messagesEndRef = useRef(null)
  const inputRef = useRef(null)

  const messages = useNetworkStore((s) => s.messages)
  const chatConnected = useNetworkStore((s) => s.chatConnected)
  const connectChat = useNetworkStore((s) => s.connectChat)
  const sendMessage = useNetworkStore((s) => s.sendMessage)
  const status = useNetworkStore((s) => s.status)

  const myNodeId = status?.node_id || ''
  const unreadCount = messages.length

  // Connect WebSocket on mount
  useEffect(() => {
    connectChat()
  }, [connectChat])

  // Auto-scroll to latest message
  useEffect(() => {
    if (isOpen && messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages, isOpen])

  // Focus input when opening
  useEffect(() => {
    if (isOpen && inputRef.current) {
      inputRef.current.focus()
    }
  }, [isOpen])

  const handleSend = () => {
    if (!input.trim()) return
    sendMessage(input)
    setInput('')
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <>
      {/* Floating Action Button */}
      {!isOpen && (
        <button
          className="chat-fab"
          onClick={() => setIsOpen(true)}
          title="Open Swarm Chat"
          id="chat-fab-button"
        >
          <MessageCircle size={24} />
          {unreadCount > 0 && (
            <span className="chat-fab-badge">{unreadCount > 99 ? '99+' : unreadCount}</span>
          )}
        </button>
      )}

      {/* Chat Drawer */}
      <div className={`chat-drawer ${isOpen ? 'chat-drawer-open' : ''}`}>
        {/* Header */}
        <div className="chat-header">
          <div className="chat-header-left">
            {chatConnected ? (
              <Wifi size={16} className="chat-status-icon connected" />
            ) : (
              <WifiOff size={16} className="chat-status-icon disconnected" />
            )}
            <span className="chat-header-title">Swarm Chat</span>
            <span className="chat-header-badge">
              {chatConnected ? 'Live' : 'Offline'}
            </span>
          </div>
          <button className="chat-close-btn" onClick={() => setIsOpen(false)}>
            <X size={18} />
          </button>
        </div>

        {/* Messages */}
        <div className="chat-messages" id="chat-messages-container">
          {messages.length === 0 ? (
            <div className="chat-empty">
              <MessageCircle size={32} strokeWidth={1} />
              <p>No messages yet</p>
              <span>Send a message to the swarm!</span>
            </div>
          ) : (
            messages.map((msg, i) => {
              const isMe = msg.sender_id === myNodeId
              return (
                <div
                  key={msg.msg_id || i}
                  className={`chat-bubble ${isMe ? 'chat-bubble-self' : 'chat-bubble-peer'}`}
                >
                  {!isMe && (
                    <div className="chat-sender">
                      {msg.sender_name || msg.sender_id?.slice(0, 8)}
                    </div>
                  )}
                  <div className="chat-text">
                    <ChatText text={msg.text} />
                  </div>
                  <div className="chat-time">{formatTime(msg.timestamp)}</div>
                </div>
              )
            })
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="chat-input-bar">
          <input
            ref={inputRef}
            type="text"
            className="chat-input"
            placeholder={chatConnected ? 'Type a message...' : 'Connecting...'}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={!chatConnected}
            id="chat-input-field"
          />
          <button
            className="chat-send-btn"
            onClick={handleSend}
            disabled={!chatConnected || !input.trim()}
            id="chat-send-button"
          >
            <Send size={18} />
          </button>
        </div>
      </div>
    </>
  )
}
