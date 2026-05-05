/**
 * DistriStore — Zustand Global State Store
 *
 * Single source of truth for node status, peers, files, and performance history.
 * Auto-polls /status and /files every 3 seconds — pages don't manage their own intervals.
 */ 

import { create } from 'zustand'
import { fetchStatus, fetchFiles, fetchAllDownloads } from '../api/client'

const MAX_HISTORY = 60 // Keep 60 data points (3min at 3s intervals)

const useNetworkStore = create((set, get) => ({
  // ── Node State ──────────────────────────────────────────────
  status: null,
  files: [],
  isConnected: false,
  lastUpdate: null,
  error: null,

  // ── Performance History (for charts) ────────────────────────
  latencyHistory: [],
  throughputHistory: [],

  // ── Phase 21: Active Downloads ──────────────────────────────
  activeDownloads: {},

  // ── Polling ─────────────────────────────────────────────────
  pollingInterval: null,

  startPolling: (intervalMs = 3000) => {
    const { pollingInterval } = get()
    if (pollingInterval) return // Already polling

    // Immediate first fetch
    get().refresh()

    const id = setInterval(() => get().refresh(), intervalMs)
    set({ pollingInterval: id })
  },

  stopPolling: () => {
    const { pollingInterval } = get()
    if (pollingInterval) {
      clearInterval(pollingInterval)
      set({ pollingInterval: null })
    }
  },

  refresh: async () => {
    try {
      const [statusData, filesData, downloadsData] = await Promise.all([
        fetchStatus(),
        fetchFiles(),
        fetchAllDownloads().catch(() => ({})),
      ])

      const now = Date.now()
      const latency = statusData.latency || 0

      set((state) => ({
        status: statusData,
        files: filesData,
        activeDownloads: downloadsData,
        isConnected: true,
        lastUpdate: now,
        error: null,
        latencyHistory: [
          ...state.latencyHistory.slice(-(MAX_HISTORY - 1)),
          { time: now, value: latency },
        ],
        throughputHistory: [
          ...state.throughputHistory.slice(-(MAX_HISTORY - 1)),
          {
            time: now,
            download: Math.random() * 80 + 20, // placeholder until real metrics
            upload: Math.random() * 40 + 10,
          },
        ],
      }))
    } catch (err) {
      set({ isConnected: false, error: err.message })
    }
  },

  // ── Computed Helpers ────────────────────────────────────────
  getPeerCount: () => {
    const { status } = get()
    return Object.keys(status?.peers || {}).length
  },

  getPeerList: () => {
    const { status } = get()
    const peers = status?.peers || {}
    return Object.entries(peers).map(([id, info]) => ({
      id,
      name: info.name || id.slice(0, 12),
      host: info.host || 'unknown',
      port: info.port || 0,
      health_score: info.health_score || 0,
      lastSeen: info.last_seen || null,
    }))
  },

  getChunkCount: () => {
    const { status } = get()
    return status?.chunk_count || status?.local_chunks?.length || 0
  },

  getStorageUsed: () => {
    const { status } = get()
    return status?.storage_used || 0
  },

  // ── Chat (Phase 19) ─────────────────────────────────────────
  messages: [],
  chatWs: null,
  chatConnected: false,

  connectChat: () => {
    const { chatWs } = get()
    if (chatWs) return // Already connected

    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const host = window.location.hostname
    const port = import.meta.env.VITE_API_PORT || '8888'
    const wsUrl = `${protocol}://${host}:${port}/ws/chat`

    const ws = new WebSocket(wsUrl)

    ws.onopen = () => {
      set({ chatConnected: true })
      console.log('[Chat] WebSocket connected')
    }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        set((state) => ({
          messages: [...state.messages.slice(-199), msg], // Keep last 200
        }))
      } catch (e) {
        console.warn('[Chat] Bad message:', e)
      }
    }

    ws.onclose = () => {
      set({ chatConnected: false, chatWs: null })
      console.log('[Chat] WebSocket disconnected, reconnecting in 3s...')
      setTimeout(() => get().connectChat(), 3000)
    }

    ws.onerror = (err) => {
      console.warn('[Chat] WebSocket error:', err)
      ws.close()
    }

    set({ chatWs: ws })
  },

  sendMessage: (text) => {
    const { chatWs, chatConnected } = get()
    if (!chatWs || !chatConnected || !text.trim()) return
    chatWs.send(JSON.stringify({ text: text.trim() }))
  },

  disconnectChat: () => {
    const { chatWs } = get()
    if (chatWs) {
      chatWs.close()
      set({ chatWs: null, chatConnected: false })
    }
  },
}))

export default useNetworkStore
