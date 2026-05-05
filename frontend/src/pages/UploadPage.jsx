/**
 * UploadPage — File upload with drag & drop, encryption, and result display.
 *
 * Phase 25C: adds a "Threshold" mode where the file's AES key is split via
 * Shamir Secret Sharing across N peers (any M reconstruct). The recipient
 * and all holders must be in accepted chat threads.
 */
 
import { useEffect, useMemo, useState } from 'react'
import { Upload, Lock, FileUp, CheckCircle2, ShieldCheck, Users } from 'lucide-react'
import { uploadFile, uploadFileThreshold, fetchChats } from '../api/client'
import useNetworkStore from '../store/useNetworkStore'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'
import CopyButton from '../components/ui/CopyButton'

function formatBytes(bytes) {
  if (!bytes) return '0 B'
  const k = 1024, sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i]
}

export default function UploadPage() {
  const [file, setFile] = useState(null)
  const [password, setPassword] = useState('')
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [dragActive, setDragActive] = useState(false)
  const refresh = useNetworkStore((s) => s.refresh)

  // ── Phase 25C: threshold mode state ────────────────────────────
  const [thresholdMode, setThresholdMode] = useState(false)
  const [chats, setChats] = useState([])
  const [recipientId, setRecipientId] = useState('')
  const [m, setM] = useState(2)
  const [n, setN] = useState(3)
  const [autoHolders, setAutoHolders] = useState(true)
  const [selectedHolders, setSelectedHolders] = useState([])  // node_ids

  // Pull accepted chats once threshold mode opens, then refresh every 5s
  useEffect(() => {
    if (!thresholdMode) return
    let alive = true
    const reload = async () => {
      try {
        const data = await fetchChats()
        if (alive) setChats(data.chats || [])
      } catch (e) {
        console.error('fetchChats failed:', e)
      }
    }
    reload()
    const id = setInterval(reload, 5000)
    return () => { alive = false; clearInterval(id) }
  }, [thresholdMode])

  // Only accepted chat partners are valid recipients
  const acceptedChats = useMemo(
    () => chats.filter((c) => c.status === 'accepted'),
    [chats],
  )

  // Holders must be accepted chat partners that are NOT the recipient
  const eligibleHolders = useMemo(
    () => acceptedChats.filter((c) => c.peer_id !== recipientId),
    [acceptedChats, recipientId],
  )

  const onlineEligibleHolders = useMemo(
    () => eligibleHolders.filter((c) => c.online),
    [eligibleHolders],
  )

  // Drop holders that are no longer eligible (e.g. when recipient changes)
  useEffect(() => {
    setSelectedHolders((prev) => prev.filter(
      (id) => eligibleHolders.some((c) => c.peer_id === id),
    ))
  }, [eligibleHolders])

  const toggleHolder = (peerId) => {
    setSelectedHolders((prev) =>
      prev.includes(peerId) ? prev.filter((p) => p !== peerId) : [...prev, peerId])
  }

  const handleUpload = async () => {
    if (!file) return
    setUploading(true); setError(null); setResult(null)
    try {
      let data
      if (thresholdMode) {
        if (!recipientId) throw new Error('Pick a recipient')
        if (!(m >= 1 && m <= n && n <= 254)) {
          throw new Error(`Invalid (M, N) = (${m}, ${n}). Need 1 ≤ M ≤ N ≤ 254.`)
        }
        if (!autoHolders && selectedHolders.length !== n) {
          throw new Error(`Pick exactly ${n} holders or enable auto-pick.`)
        }
        data = await uploadFileThreshold(
          file, recipientId, m, n,
          autoHolders ? [] : selectedHolders,
        )
      } else {
        data = await uploadFile(file, password)
      }
      setResult(data)
      refresh()
    } catch (err) {
      setError(err.message)
    } finally {
      setUploading(false)
    }
  }

  return (
    <Card title="Upload File" icon={<Upload size={20} />}>
      <div className="form-section">
        {/* Mode tabs */}
        <div className="upload-mode-tabs">
          <button
            type="button"
            className={`upload-mode-tab ${!thresholdMode ? 'is-active' : ''}`}
            onClick={() => setThresholdMode(false)}
          >
            <Lock size={14} /> Password
          </button>
          <button
            type="button"
            className={`upload-mode-tab ${thresholdMode ? 'is-active' : ''}`}
            onClick={() => setThresholdMode(true)}
          >
            <ShieldCheck size={14} /> Threshold (M-of-N)
          </button>
        </div>

        {/* Drop Zone */}
        <div
          className={`drop-zone ${dragActive ? 'drag-active' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setDragActive(true) }}
          onDragLeave={() => setDragActive(false)}
          onDrop={(e) => { e.preventDefault(); setDragActive(false); setFile(e.dataTransfer.files?.[0] || null) }}
          onClick={() => document.getElementById('file-input').click()}
        >
          <div className="drop-zone-icon">{file ? <CheckCircle2 size={36} /> : <FileUp size={36} />}</div>
          <div className="drop-zone-text">{file ? file.name : 'Drop file here or click to browse'}</div>
          <div className="drop-zone-hint">{file ? formatBytes(file.size) : 'AES-256-GCM encrypted'}</div>
          <input id="file-input" type="file" style={{ display: 'none' }} onChange={(e) => setFile(e.target.files?.[0] || null)} />
        </div>

        {!thresholdMode && (
          <div className="input-group">
            <label><Lock size={14} /> Encryption Password (optional)</label>
            <input type="password" className="input-field" placeholder="Leave empty for no encryption" value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
        )}

        {thresholdMode && (
          <div className="threshold-config">
            <div className="threshold-explainer">
              <ShieldCheck size={14} />
              <span>
                The file's AES key is split into <strong>N</strong> shares; any{' '}
                <strong>M</strong> holders can reconstruct it together with the
                recipient. No single peer can decrypt alone.
              </span>
            </div>

            {/* Recipient */}
            <div className="input-group">
              <label><Users size={14} /> Recipient</label>
              {acceptedChats.length === 0 ? (
                <div className="threshold-empty">
                  No accepted chats yet — invite a peer in the Chats page first.
                </div>
              ) : (
                <select
                  className="input-field"
                  value={recipientId}
                  onChange={(e) => setRecipientId(e.target.value)}
                >
                  <option value="">— pick a recipient —</option>
                  {acceptedChats.map((c) => (
                    <option key={c.peer_id} value={c.peer_id}>
                      {c.peer_name || c.peer_id.slice(0, 16)}{c.online ? ' (online)' : ' (offline)'}
                    </option>
                  ))}
                </select>
              )}
            </div>

            {/* M / N */}
            <div className="threshold-mn-grid">
              <div className="input-group">
                <label>Threshold M (shares needed)</label>
                <input
                  type="number"
                  min={1}
                  max={n}
                  className="input-field"
                  value={m}
                  onChange={(e) => setM(Math.max(1, Math.min(n, parseInt(e.target.value || '1', 10))))}
                />
              </div>
              <div className="input-group">
                <label>Total holders N</label>
                <input
                  type="number"
                  min={m}
                  max={254}
                  className="input-field"
                  value={n}
                  onChange={(e) => setN(Math.max(m, Math.min(254, parseInt(e.target.value || '1', 10))))}
                />
              </div>
            </div>

            {/* Holder selection */}
            <div className="input-group">
              <label className="threshold-holder-label">
                <span><Users size={14} /> Share holders</span>
                <label className="threshold-auto-toggle">
                  <input
                    type="checkbox"
                    checked={autoHolders}
                    onChange={(e) => setAutoHolders(e.target.checked)}
                  />
                  <span>Auto-pick</span>
                </label>
              </label>
              {autoHolders ? (
                <div className="threshold-empty">
                  Holders will be auto-picked from accepted chat partners (excluding the recipient)
                  who are online and have a known onion-routing pubkey.
                  Need at least <strong>{n}</strong> eligible peer{n === 1 ? '' : 's'}; currently online: <strong>{onlineEligibleHolders.length}</strong>.
                </div>
              ) : (
                <div className="threshold-holder-list">
                  {eligibleHolders.length === 0 ? (
                    <div className="threshold-empty">No eligible peers — invite more peers via Chats.</div>
                  ) : (
                    eligibleHolders.map((c) => (
                      <label key={c.peer_id} className={`threshold-holder-pill ${selectedHolders.includes(c.peer_id) ? 'is-selected' : ''} ${!c.online ? 'is-offline' : ''}`}>
                        <input
                          type="checkbox"
                          checked={selectedHolders.includes(c.peer_id)}
                          onChange={() => toggleHolder(c.peer_id)}
                          disabled={!c.online && !selectedHolders.includes(c.peer_id)}
                        />
                        <span className={`chat-online-dot ${c.online ? 'is-online' : ''}`} />
                        <span>{c.peer_name || c.peer_id.slice(0, 12)}</span>
                      </label>
                    ))
                  )}
                  <div className="threshold-holder-counter">
                    Selected: <strong>{selectedHolders.length}</strong> / {n}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Upload Button */}
        <Button variant="primary" loading={uploading} disabled={!file || (thresholdMode && !recipientId)} onClick={handleUpload}>
          {thresholdMode ? `Upload (${m}-of-${n} threshold)` : 'Upload file'}
        </Button>

        {/* Success */}
        {result && (
          <div className="alert alert-success">
            <div style={{ flex: 1 }}>
              <div>
                Upload complete. {result.chunks} chunks
                {result.manifest?.merkle_root && <> · Merkle: {result.manifest.merkle_root.slice(0, 16)}...</>}
                {result.key_scheme === 'shamir' && (
                  <> · {result.key_m}-of-{result.key_n} threshold key distributed</>
                )}
              </div>
              {result.share_holders && result.share_holders.length > 0 && (
                <div className="upload-holder-list">
                  Held by: {result.share_holders.map((h) => h.holder_name || h.holder_id.slice(0, 12)).join(', ')}
                </div>
              )}
              <div className="upload-hash-row">
                <span className="upload-hash-text">{result.file_hash}</span>
                <CopyButton text={result.file_hash} label="Copy Hash" />
              </div>
            </div>
          </div>
        )}

        {/* Error */}
        {error && <div className="alert alert-error">{error}</div>}
      </div>
    </Card>
  )
}
