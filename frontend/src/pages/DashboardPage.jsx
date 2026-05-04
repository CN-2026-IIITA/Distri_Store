/**
 * DashboardPage — Main overview with stats, topology, peers, charts, and file list.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Globe, Clock, Database, HardDrive, Folder, FileText, Inbox, Eye, ArrowDownToLine, KeyRound } from 'lucide-react'
import useNetworkStore from '../store/useNetworkStore'
import StatCard from '../components/ui/StatCard'
import Card from '../components/ui/Card'
import CopyButton from '../components/ui/CopyButton'
import PreviewModal, { isPreviewable } from '../components/ui/PreviewModal'
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

      {/* Stored Files */}
      <Card title="Stored Files" icon={<Folder size={18} />}>
        {files.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon"><Inbox size={36} strokeWidth={1.5} /></div>
            <p>No files stored yet</p>
          </div>
        ) : (
          <div className="file-list">
            {files.map((f, i) => (
              <div className="file-item" key={i}>
                <div className="file-info">
                  <div className="file-icon"><FileText size={18} /></div>
                  <div>
                    <div className="file-name">{f.filename}</div>
                    <div className="file-meta">
                      {formatBytes(f.size)} · {f.chunks} chunks
                      {f.merkle_root ? ` · Merkle: ${f.merkle_root.slice(0, 12)}...` : ''}
                    </div>
                  </div>
                </div>
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
              </div>
            ))}
          </div>
        )}
      </Card>

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
