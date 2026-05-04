/**
 * DashboardPage — Main overview with stats, topology, peers, charts, and file list.
 */

import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Globe, Clock, Database, HardDrive, Folder, FileText, Inbox, Eye, ArrowDownToLine, KeyRound, Send, CheckSquare, Square, Route } from 'lucide-react'
import useNetworkStore from '../store/useNetworkStore'
import { fetchShareReceipts } from '../api/client'
import StatCard from '../components/ui/StatCard'
import Card from '../components/ui/Card'
import CopyButton from '../components/ui/CopyButton'
import PreviewModal, { isPreviewable } from '../components/ui/PreviewModal'
import ShareModal from '../components/ui/ShareModal'
import RoutePath from '../components/ui/RoutePath'
import NetworkTopology from '../components/network/NetworkTopology'
import PeerTable from '../components/network/PeerTable'
import TransferSpeedChart from '../components/network/TransferSpeedChart'
import ActiveDownloads from '../components/network/ActiveDownloads'

function formatBytes(bytes) {
  if (!bytes) return '0 B'
  const k = 1024, sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i]
}

function formatUptime(seconds) {
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = Math.floor(seconds % 60)
  return h > 0 ? `${h}h ${m}m` : m > 0 ? `${m}m ${s}s` : `${s}s`
}

export default function DashboardPage() {
  const status = useNetworkStore((s) => s.status)
  const files = useNetworkStore((s) => s.files)
  const navigate = useNavigate()
  const peerCount = useNetworkStore((s) => s.getPeerCount())

  // Phase 20: Preview modal state
  const [previewFile, setPreviewFile] = useState(null)
  const [previewPassword, setPreviewPassword] = useState('')
  const [showPasswordPrompt, setShowPasswordPrompt] = useState(null)

  // Phase 24C: multi-select + share modal state
  const [selectMode, setSelectMode] = useState(false)
  const [selectedHashes, setSelectedHashes] = useState(() => new Set())
  const [showShareModal, setShowShareModal] = useState(false)

  const toggleSelect = (hash) => {
    setSelectedHashes((prev) => {
      const next = new Set(prev)
      if (next.has(hash)) next.delete(hash); else next.add(hash)
      return next
    })
  }

  const exitSelectMode = () => {
    setSelectMode(false)
    setSelectedHashes(new Set())
  }

  const filesToShare = files.filter((f) => selectedHashes.has(f.file_hash))

  // Phase 25A: poll incoming delivery receipts so we can show "X downloaded
  // your shared file via path Y" on the dashboard.
  const [receipts, setReceipts] = useState([])
  useEffect(() => {
    let cancelled = false
    const reload = async () => {
      try {
        const r = await fetchShareReceipts()
        if (!cancelled) setReceipts(r)
      } catch (e) {
        if (!cancelled) console.error('fetchShareReceipts failed:', e)
      }
    }
    reload()
    const id = setInterval(reload, 7000)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  const filenameByHash = files.reduce((acc, f) => { acc[f.file_hash] = f.filename; return acc }, {})

  const handleSelectFile = (hash) => {
    navigate(`/download?hash=${hash}`)
  }

  const handlePreview = (file) => {
    // All files are encrypted by default in DistriStore
    setShowPasswordPrompt(file)
  }

  const handlePasswordSubmit = () => {
    setPreviewFile(showPasswordPrompt)
    setShowPasswordPrompt(null)
  }

  return (
    <div>
      {/* Stats Grid */}
      <div className="stats-grid">
        <StatCard label="Connected peers" value={peerCount} icon={<Globe size={20} />} color="var(--gradient-primary)" />
        <StatCard label="Uptime" value={formatUptime(status?.uptime_seconds || 0)} icon={<Clock size={20} />} color="linear-gradient(135deg, #06b6d4, #10b981)" />
        <StatCard label="Stored chunks" value={useNetworkStore.getState().getChunkCount()} icon={<Database size={20} />} color="linear-gradient(135deg, #f43f5e, #ec4899)" />
        <StatCard label="Storage used" value={formatBytes(useNetworkStore.getState().getStorageUsed())} icon={<HardDrive size={20} />} color="linear-gradient(135deg, #8b5cf6, #6366f1)" />
      </div>

      {/* Network Visualizations */}
      <NetworkTopology />
      <TransferSpeedChart />

      {/* Peer Table */}
      <PeerTable />

      {/* Phase 21: Active Downloads */}
      <ActiveDownloads />

      {/* Phase 25A: Delivery receipts — only render the card if we have any */}
      {receipts.length > 0 && (
        <Card title="Delivery receipts" icon={<Route size={18} />}>
          <div className="receipts-list">
            {receipts.map((r) => {
              const ts = new Date((r.received_at || 0) * 1000)
              const when = ts.toLocaleString([], {
                month: 'short', day: 'numeric',
                hour: '2-digit', minute: '2-digit',
              })
              return (
                <div className="receipt-row" key={r.id}>
                  <div className="receipt-row-header">
                    <span className="receipt-from">
                      <strong>{r.receiver_name || r.receiver_id.slice(0, 12) + '...'}</strong> downloaded{' '}
                      <code>{filenameByHash[r.file_hash] || r.file_hash.slice(0, 16) + '...'}</code>
                    </span>
                    <span className="receipt-when">{when}</span>
                  </div>
                  <RoutePath hops={r.path} prefixSelf={false} />
                </div>
              )
            })}
          </div>
        </Card>
      )}

      {/* Stored Files */}
      <Card title="Stored Files" icon={<Folder size={18} />}>
        {files.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon"><Inbox size={36} strokeWidth={1.5} /></div>
            <p>No files stored yet</p>
          </div>
        ) : (
          <>
            {/* Select-mode toolbar */}
            <div className="files-toolbar">
              {!selectMode ? (
                <button className="btn-sm" onClick={() => setSelectMode(true)}>
                  <CheckSquare size={14} /> Select to share
                </button>
              ) : (
                <>
                  <span className="files-toolbar-count">
                    {selectedHashes.size} selected
                  </span>
                  <button
                    className="btn btn-primary"
                    style={{ padding: '6px 14px', fontSize: 13 }}
                    onClick={() => setShowShareModal(true)}
                    disabled={selectedHashes.size === 0}
                  >
                    <Send size={14} /> Share {selectedHashes.size > 0 ? `(${selectedHashes.size})` : ''}
                  </button>
                  <button className="btn-sm" onClick={exitSelectMode}>
                    Cancel
                  </button>
                </>
              )}
            </div>

            <div className="file-list">
              {files.map((f, i) => {
                const checked = selectedHashes.has(f.file_hash)
                return (
                  <div
                    className={`file-item ${selectMode ? 'file-item-selectable' : ''} ${checked ? 'file-item-selected' : ''}`}
                    key={i}
                    onClick={selectMode ? () => toggleSelect(f.file_hash) : undefined}
                  >
                    <div className="file-info">
                      {selectMode && (
                        <button
                          className="file-checkbox"
                          onClick={(e) => { e.stopPropagation(); toggleSelect(f.file_hash) }}
                          title={checked ? 'Unselect' : 'Select'}
                        >
                          {checked ? <CheckSquare size={20} /> : <Square size={20} />}
                        </button>
                      )}
                      <div className="file-icon"><FileText size={18} /></div>
                      <div>
                        <div className="file-name">{f.filename}</div>
                        <div className="file-meta">
                          {formatBytes(f.size)} · {f.chunks} chunks
                          {f.merkle_root ? ` · Merkle: ${f.merkle_root.slice(0, 12)}...` : ''}
                        </div>
                      </div>
                    </div>
                    {!selectMode && (
                      <div className="file-actions">
                        <div className="file-hash">{f.file_hash?.slice(0, 20)}...</div>
                        <div className="file-buttons">
                          <CopyButton text={f.file_hash} label="Copy Hash" />
                          {isPreviewable(f.filename) && (
                            <button
                              className="btn-copy btn-preview"
                              onClick={() => handlePreview(f)}
                              title="Preview this file"
                            >
                              <Eye size={14} /> Preview
                            </button>
                          )}
                          <button
                            className="btn-copy btn-dl"
                            onClick={() => handleSelectFile(f.file_hash)}
                            title="Download this file"
                          >
                            <ArrowDownToLine size={14} /> Download
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </>
        )}
      </Card>

      {/* Phase 24C: Share-to-peer modal */}
      {showShareModal && (
        <ShareModal
          files={filesToShare}
          onClose={() => setShowShareModal(false)}
          onShared={() => { exitSelectMode(); }}
        />
      )}

      {/* Password Prompt for encrypted preview */}
      {showPasswordPrompt && (
        <div className="preview-overlay" onClick={() => setShowPasswordPrompt(null)}>
          <div className="preview-password-dialog" onClick={(e) => e.stopPropagation()}>
            <h3 style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <KeyRound size={18} /> Enter decryption password
            </h3>
            <p className="preview-password-filename">{showPasswordPrompt.filename}</p>
            <input
              type="password"
              className="input-field"
              placeholder="Password..."
              value={previewPassword}
              onChange={(e) => setPreviewPassword(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handlePasswordSubmit()}
              autoFocus
            />
            <div className="preview-password-actions">
              <button className="btn btn-primary" onClick={handlePasswordSubmit}>
                Preview
              </button>
              <button
                className="btn"
                style={{ background: 'rgba(255,255,255,0.08)', color: 'var(--text-secondary)' }}
                onClick={() => setShowPasswordPrompt(null)}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Preview Modal */}
      <PreviewModal
        isOpen={!!previewFile}
        onClose={() => { setPreviewFile(null); setPreviewPassword(''); }}
        fileHash={previewFile?.file_hash || ''}
        filename={previewFile?.filename || ''}
        password={previewPassword}
      />
    </div>
  )
}
