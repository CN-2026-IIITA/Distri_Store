/**
 * ActiveDownloads — Phase 21: Resumable download progress tracker.
 *
 * Displays active, paused, and completed downloads with progress bars
 * and Pause / Resume controls. Includes password prompt for encrypted resumes.
 */

import { useState } from 'react'
import { Pause, Play, X, Download, CheckCircle, AlertCircle, Loader, Lock, KeyRound } from 'lucide-react'
import { pauseDownload, resumeDownload, clearCompletedDownloads } from '../../api/client'
import useNetworkStore from '../../store/useNetworkStore'
import Card from '../ui/Card'

const STATUS_CONFIG = {
  downloading: { icon: Loader, color: 'var(--accent-cyan)', label: 'Downloading' },
  paused:      { icon: Pause, color: 'var(--accent-amber)', label: 'Paused' },
  completed:   { icon: CheckCircle, color: 'var(--accent-green)', label: 'Completed' },
  error:       { icon: AlertCircle, color: 'var(--accent-rose)', label: 'Error' },
  pending:     { icon: Loader, color: 'var(--text-muted)', label: 'Pending' },
}

function formatBytes(bytes) {
  if (!bytes) return '0 B'
  const k = 1024, sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i]
}

export default function ActiveDownloads() {
  const activeDownloads = useNetworkStore((s) => s.activeDownloads)
  const [loading, setLoading] = useState({})
  const [resumePrompt, setResumePrompt] = useState(null)
  const [resumePassword, setResumePassword] = useState('')

  const entries = Object.entries(activeDownloads || {})
  if (entries.length === 0) return null // Don't render card if no downloads

  const handlePause = async (fileHash) => {
    setLoading((l) => ({ ...l, [fileHash]: true }))
    try {
      await pauseDownload(fileHash)
    } catch (e) {
      console.error('Pause failed:', e)
    }
    setLoading((l) => ({ ...l, [fileHash]: false }))
  }

  const handleResumeClick = (fileHash, filename) => {
    // Show password prompt for encrypted files
    setResumePrompt({ fileHash, filename })
    setResumePassword('')
  }

  const handleResumeConfirm = async () => {
    if (!resumePrompt) return
    const { fileHash } = resumePrompt
    setLoading((l) => ({ ...l, [fileHash]: true }))
    try {
      await resumeDownload(fileHash, resumePassword)
    } catch (e) {
      console.error('Resume failed:', e)
    }
    setLoading((l) => ({ ...l, [fileHash]: false }))
    setResumePrompt(null)
    setResumePassword('')
  }

  const handleClear = async () => {
    try {
      await clearCompletedDownloads()
    } catch (e) {
      console.error('Clear failed:', e)
    }
  }

  const hasCompleted = entries.some(([, d]) => d.status === 'completed' || d.status === 'error')

  return (
    <>
      <Card
        title="Active Downloads"
        icon={<Download size={20} />}
        action={hasCompleted ? (
          <button className="btn btn-sm" onClick={handleClear} title="Clear finished downloads">
            <X size={14} /> Clear Done
          </button>
        ) : null}
      >
        <div className="downloads-list">
          {entries.map(([fileHash, dl]) => {
            const cfg = STATUS_CONFIG[dl.status] || STATUS_CONFIG.pending
            const StatusIcon = cfg.icon
            const isActive = dl.status === 'downloading'
            const isPaused = dl.status === 'paused'
            const isLoading = loading[fileHash]

            return (
              <div className="download-item" key={fileHash}>
                {/* Header row */}
                <div className="download-item-header">
                  <div className="download-item-info">
                    <StatusIcon
                      size={16}
                      className={isActive ? 'spin-slow' : ''}
                      style={{ color: cfg.color, flexShrink: 0 }}
                    />
                    <span className="download-filename">{dl.filename}</span>
                    <span className="download-status-badge" style={{ background: cfg.color + '20', color: cfg.color }}>
                      {cfg.label}
                    </span>
                  </div>

                  <div className="download-item-actions">
                    {isActive && (
                      <button
                        className="download-ctrl-btn download-pause-btn"
                        onClick={() => handlePause(fileHash)}
                        disabled={isLoading}
                        title="Pause download"
                      >
                        <Pause size={14} /> Pause
                      </button>
                    )}
                    {isPaused && (
                      <button
                        className="download-ctrl-btn download-resume-btn"
                        onClick={() => handleResumeClick(fileHash, dl.filename)}
                        disabled={isLoading}
                        title="Resume download"
                      >
                        <Play size={14} /> Resume
                      </button>
                    )}
                  </div>
                </div>

                {/* Progress bar */}
                <div className="download-progress-track">
                  <div
                    className="download-progress-fill"
                    style={{
                      width: `${dl.progress || 0}%`,
                      background: dl.status === 'error'
                        ? 'var(--accent-rose)'
                        : dl.status === 'completed'
                          ? 'var(--accent-green)'
                          : 'var(--gradient-primary)',
                    }}
                  />
                </div>

                {/* Stats row */}
                <div className="download-stats">
                  <span>{dl.downloaded_chunks} / {dl.total_chunks} chunks</span>
                  <span>{formatBytes(dl.total_size)}</span>
                  <span className="download-progress-pct">{dl.progress}%</span>
                </div>

                {/* Error message */}
                {dl.error_message && (
                  <div className="download-error">{dl.error_message}</div>
                )}
              </div>
            )
          })}
        </div>
      </Card>

      {/* Resume password prompt */}
      {resumePrompt && (
        <div className="preview-overlay" onClick={() => setResumePrompt(null)}>
          <div className="preview-password-dialog" onClick={(e) => e.stopPropagation()}>
            <h3 style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <KeyRound size={18} /> Resume — Enter password
            </h3>
            <p className="preview-password-filename">{resumePrompt.filename}</p>
            <input
              type="password"
              className="input-field"
              placeholder="Decryption password..."
              value={resumePassword}
              onChange={(e) => setResumePassword(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleResumeConfirm()}
              autoFocus
            />
            <div className="preview-password-actions">
              <button className="btn btn-primary" onClick={handleResumeConfirm}>
                <Play size={14} /> Resume
              </button>
              <button
                className="btn"
                style={{ background: 'rgba(255,255,255,0.08)', color: 'var(--text-secondary)' }}
                onClick={() => setResumePrompt(null)}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
