/**
 * DistriStore — Centralized API Service Layer
 *
 * Singleton Axios instance with base URL configuration.
 * Components must NEVER call Axios directly — import these functions instead.
 */

import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_URL || `http://${window.location.hostname}:8888`

const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
  headers: { 'Accept': 'application/json' },
})

// ── Request interceptor (logging, auth tokens in future) ──────
api.interceptors.request.use((config) => {
  config.metadata = { startTime: performance.now() }
  return config
})

// ── Response interceptor (timing, error normalization) ────────
api.interceptors.response.use(
  (response) => {
    const elapsed = performance.now() - response.config.metadata.startTime
    response.latency = Math.round(elapsed)
    return response
  },
  (error) => {
    const message = error.response?.data?.detail
      || error.response?.data?.message
      || error.message
      || 'Network error'
    return Promise.reject(new Error(message))
  }
)

// ── Node Status & Peers ───────────────────────────────────────

export async function fetchStatus() {
  const { data, latency } = await api.get('/status')
  return { ...data, latency }
}

export async function fetchFiles() {
  const { data } = await api.get('/files')
  return data.files || []
}

export async function fetchManifest(fileHash) {
  const { data } = await api.get(`/manifest/${fileHash}`)
  return data
}

export async function fetchChunk(chunkHash) {
  const { data } = await api.get(`/chunk/${chunkHash}`, { responseType: 'arraybuffer' })
  return data
}

// ── Upload ────────────────────────────────────────────────────

export async function uploadFile(file, password = '') {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('password', password)

  const { data, latency } = await api.post('/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
  return { ...data, latency }
}

// ── Download ──────────────────────────────────────────────────

export async function downloadFile(fileHash, password = '') {
  const params = password ? { password } : {}
  try {
    const response = await api.get(`/download/${fileHash}`, {
      params,
      responseType: 'blob',
      timeout: 120000,
    })

    // Extract filename from Content-Disposition header
    const cd = response.headers['content-disposition'] || ''
    const match = cd.match(/filename="?([^"]+)"?/)
    const filename = match ? match[1] : 'download.bin'

    return { blob: response.data, filename, latency: response.latency }
  } catch (err) {
    // Axios returns error responses as blobs when responseType is 'blob'
    // We need to read the blob to extract the actual error message
    if (err.response?.data instanceof Blob) {
      try {
        const text = await err.response.data.text()
        const json = JSON.parse(text)
        throw new Error(json.detail || json.message || text)
      } catch (parseErr) {
        if (parseErr.message && !parseErr.message.includes('JSON')) {
          throw parseErr // Re-throw our parsed error
        }
      }
    }
    throw err // Fallback to original error
  }
}

// ── Utility: trigger browser download from blob ───────────────

export function triggerBlobDownload(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(url), 100)
}

// ── Phase 21: Resumable Downloads ─────────────────────────────

export async function startResumableDownload(fileHash, password = '') {
  const params = password ? { password } : {}
  const { data } = await api.post(`/download/${fileHash}/start`, null, { params })
  return data
}

export async function pauseDownload(fileHash) {
  const { data } = await api.post(`/download/${fileHash}/pause`)
  return data
}

export async function resumeDownload(fileHash, password = '') {
  const params = password ? { password } : {}
  const { data } = await api.post(`/download/${fileHash}/resume`, null, { params })
  return data
}

export async function fetchDownloadProgress(fileHash) {
  const { data } = await api.get(`/download/${fileHash}/progress`)
  return data.download
}

export async function fetchAllDownloads() {
  const { data } = await api.get('/downloads')
  return data.downloads || {}
}

export async function clearCompletedDownloads() {
  const { data } = await api.post('/downloads/clear')
  return data
}

/**
 * Phase 21: Fetch the merged file from a completed resumable download.
 * Uses native browser navigation to prevent buffering huge files in memory.
 */
export function downloadCompletedFile(fileHash) {
  const url = `${API_BASE}/download/${fileHash}/file`
  const a = document.createElement('a')
  a.href = url
  // The server's Content-Disposition header will provide the correct filename
  a.download = ''
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

// ── Phase 24A: 1:1 Chats (invite + accept + send) ─────────────

export async function fetchChats() {
  const { data } = await api.get('/chats')
  return data
}

export async function inviteChat(peerId) {
  const { data } = await api.post('/chats/invite', { peer_id: peerId })
  return data
}

export async function acceptChat(peerId) {
  const { data } = await api.post(`/chats/${peerId}/accept`)
  return data
}

export async function rejectChat(peerId) {
  const { data } = await api.post(`/chats/${peerId}/reject`)
  return data
}

export async function deleteChat(peerId) {
  const { data } = await api.delete(`/chats/${peerId}`)
  return data
}

export async function sendChatMessage(peerId, text) {
  const { data } = await api.post(`/chats/${peerId}/messages`, { text })
  return data
}

export async function fetchChatMessages(peerId) {
  const { data } = await api.get(`/chats/${peerId}/messages`)
  return data
}

// ── Phase 24C: Selective file sharing ─────────────────────────

export async function shareFiles(toPeerId, fileHashes, note = '') {
  const { data } = await api.post('/share', {
    to_peer_id: toPeerId, file_hashes: fileHashes, note,
  })
  return data
}

export async function fetchShares() {
  const { data } = await api.get('/shares')
  return data.shares || []
}

export async function deleteShare(shareId) {
  const { data } = await api.delete(`/shares/${shareId}`)
  return data
}

// ── Phase 25A: Onion-routing path inspection + delivery receipts ──

export async function fetchDownloadPath(fileHash) {
  const { data } = await api.get(`/download/${fileHash}/path`)
  return data
}

export async function fetchSharePath(shareId) {
  const { data } = await api.get(`/shares/${shareId}/path`)
  return data
}

export async function ackShareDelivery(shareId) {
  const { data } = await api.post(`/shares/${shareId}/ack`)
  return data
}

export async function fetchShareReceipts() {
  const { data } = await api.get('/share-receipts')
  return data.receipts || []
}

// ── Phase 25C: Threshold-Encrypted Files (Shamir Secret Sharing) ──

export async function uploadFileThreshold(file, recipientId, m, n, holderIds = []) {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('recipient_id', recipientId)
  formData.append('m', String(m))
  formData.append('n', String(n))
  if (holderIds && holderIds.length) {
    formData.append('holder_ids', holderIds.join(','))
  }
  const { data } = await api.post('/upload-threshold', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
  return data
}

export async function fetchThresholdProbe(fileHash) {
  const { data } = await api.get(`/threshold/${fileHash}/probe`)
  return data
}

// ── Phase 25B: Proof-of-Storage Audits ────────────────────────

export async function fetchAuditReputation() {
  const { data } = await api.get('/audit/reputation')
  return data.reputation || []
}

export async function fetchAuditLog(limit = 100, peerId = '') {
  const params = peerId ? { limit, peer_id: peerId } : { limit }
  const { data } = await api.get('/audit/log', { params })
  return data.log || []
}

export async function runAuditAgainst(peerId) {
  const { data } = await api.post(`/audit/run/${peerId}`)
  return data.audit
}

export async function runAuditRandom() {
  const { data } = await api.post('/audit/run')
  return data.audit
}

export default api
