/**
 * DownloadPage — Download or Preview file by hash with decryption support.
 * Phase 21: Adds resumable downloads with inline progress, pause/resume controls.
 * Reads prefilled hash from URL query params (from dashboard file list clicks).
 */

import { useState, useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Download, Lock, Play, Pause, CheckCircle, AlertCircle, Loader, FileDown } from 'lucide-react'
import {
  downloadFile, triggerBlobDownload,
  startResumableDownload, pauseDownload, resumeDownload,
  downloadCompletedFile,
} from '../api/client'
import useNetworkStore from '../store/useNetworkStore'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'
import PreviewModal from '../components/ui/PreviewModal'
import ActiveDownloads from '../components/network/ActiveDownloads'

const API_BASE = import.meta.env.VITE_API_URL || `http://${window.location.hostname}:8888`

function formatBytes(bytes) {
  if (!bytes) return '0 B'
  const k = 1024, sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i]
}

export default function DownloadPage() {
  const [searchParams] = useSearchParams()
  const [hash, setHash] = useState(() => searchParams.get('hash') || '')
  const [password, setPassword] = useState('')
  const [downloading, setDownloading] = useState(false)
  const [error, setError] = useState(null)
  const [success, setSuccess] = useState(false)

  // Phase 20: Preview state
  const [showPreview, setShowPreview] = useState(false)
  const [previewFilename, setPreviewFilename] = useState('')

  // Phase 21: Resumable download state
  const [starting, setStarting] = useState(false)
  const [fetchingFile, setFetchingFile] = useState(false)
  const activeDownloads = useNetworkStore((s) => s.activeDownloads)

  // Track which hashes we've already auto-downloaded to avoid duplicates
  const autoDownloaded = useRef(new Set())

  // Track the current hash's download if it exists
  const currentDl = hash ? (activeDownloads || {})[hash] : null

  // Auto-download the file when a resumable download completes
  useEffect(() => {
    if (
      currentDl &&
      currentDl.status === 'completed' &&
      currentDl.output_path &&
      hash &&
      !autoDownloaded.current.has(hash)
    ) {
      autoDownloaded.current.add(hash)
      handleFetchCompleted()
    }
  }, [currentDl?.status])

  // ── Instant download (original) ──────────────────────────────
  const handleDownload = async () => {
    if (!hash) return
    setDownloading(true); setError(null); setSuccess(false)
    try {
      const { blob, filename } = await downloadFile(hash, password)
      triggerBlobDownload(blob, filename)
      setSuccess(true)
    } catch (err) {
      setError(err.message)
    } finally {
      setDownloading(false)
    }
  }

  // ── Start resumable download (Phase 21) ──────────────────────
  const handleStartResumable = async () => {
    if (!hash) return
    setStarting(true); setError(null); setSuccess(false)
    autoDownloaded.current.delete(hash) // Allow auto-download for this new attempt
    try {
      await startResumableDownload(hash, password)
    } catch (err) {
      setError(err.message)
    } finally {
      setStarting(false)
    }
  }

  const handlePause = async () => {
    if (!hash) return
    setError(null)
    try {
      await pauseDownload(hash)
    } catch (err) {
      setError(err.message)
    }
  }

  const handleResume = async () => {
    if (!hash) return
    setError(null)
    autoDownloaded.current.delete(hash) // Allow auto-download after resume
    try {
      await resumeDownload(hash, password)
    } catch (err) {
      setError(err.message)
    }
  }

  // ── Fetch the completed merged file ──────────────────────────
  const handleFetchCompleted = () => {
    if (!hash) return
    try {
      downloadCompletedFile(hash)
      setSuccess(true)
    } catch (err) {
      setError(err.message)
    }
  }

  // ── Preview (Phase 20) ───────────────────────────────────────
  const handlePreview = async () => {
    if (!hash) return
    setError(null)
    try {
      const resp = await fetch(`${API_BASE}/manifest/${hash}`)
      if (resp.ok) {
        const manifest = await resp.json()
        setPreviewFilename(manifest.original_filename || 'file.bin')
      } else {
        setPreviewFilename('file.bin')
      }
    } catch {
      setPreviewFilename('file.bin')
    }
    setShowPreview(true)
  }

  return (
    <>
      <Card title="Download File" icon={<Download size={20} />}>
        <div className="form-section">
          <div className="input-group">
            <label>File Hash</label>
            <input type="text" className="input-field" placeholder="Enter SHA-256 file hash..." value={hash} onChange={(e) => setHash(e.target.value)} />
          </div>
          <div className="input-group">
            <label><Lock size={14} /> Decryption Password (if encrypted)</label>
            <input type="password" className="input-field" placeholder="Leave empty if not encrypted" value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          <div className="download-actions">
            <Button variant="success" loading={downloading} disabled={!hash} onClick={handleDownload}>
              Instant download
            </Button>
            <Button variant="primary" loading={starting} disabled={!hash} onClick={handleStartResumable}>
              Resumable download
            </Button>
            <Button variant="primary" disabled={!hash} onClick={handlePreview}>
              Preview
            </Button>
          </div>

          {/* Phase 21: Inline progress for current hash */}
          {currentDl && (
            <div className="download-inline-progress">
              <div className="download-item" style={{ marginTop: '16px' }}>
                <div className="download-item-header">
                  <div className="download-item-info">
                    {currentDl.status === 'downloading' && <Loader size={16} className="spin-slow" style={{ color: 'var(--accent-cyan)', flexShrink: 0 }} />}
                    {currentDl.status === 'finalizing' && <Loader size={16} className="spin-slow" style={{ color: 'var(--accent-purple)', flexShrink: 0 }} />}
                    {currentDl.status === 'paused' && <Pause size={16} style={{ color: 'var(--accent-amber)', flexShrink: 0 }} />}
                    {currentDl.status === 'completed' && <CheckCircle size={16} style={{ color: 'var(--accent-green)', flexShrink: 0 }} />}
                    {currentDl.status === 'error' && <AlertCircle size={16} style={{ color: 'var(--accent-rose)', flexShrink: 0 }} />}
                    <span className="download-filename">{currentDl.filename}</span>
                    <span
                      className="download-status-badge"
                      style={{
                        background:
                          currentDl.status === 'downloading' ? 'rgba(6,182,212,0.15)' :
                          currentDl.status === 'finalizing' ? 'rgba(168,85,247,0.15)' :
                          currentDl.status === 'paused' ? 'rgba(245,158,11,0.15)' :
                          currentDl.status === 'completed' ? 'rgba(16,185,129,0.15)' :
                          'rgba(244,63,94,0.15)',
                        color:
                          currentDl.status === 'downloading' ? 'var(--accent-cyan)' :
                          currentDl.status === 'finalizing' ? 'var(--accent-purple)' :
                          currentDl.status === 'paused' ? 'var(--accent-amber)' :
                          currentDl.status === 'completed' ? 'var(--accent-green)' :
                          'var(--accent-rose)',
                      }}
                    >
                      {currentDl.status === 'finalizing' ? 'Merging...' : currentDl.status}
                    </span>
                  </div>

                  <div className="download-item-actions">
                    {currentDl.status === 'downloading' && (
                      <button className="download-ctrl-btn download-pause-btn" onClick={handlePause}>
                        <Pause size={14} /> Pause
                      </button>
                    )}
                    {currentDl.status === 'paused' && (
                      <button className="download-ctrl-btn download-resume-btn" onClick={handleResume}>
                        <Play size={14} /> Resume
                      </button>
                    )}
                    {currentDl.status === 'completed' && (
                      <button
                        className="download-ctrl-btn download-resume-btn"
                        onClick={handleFetchCompleted}
                        disabled={fetchingFile}
                      >
                        <FileDown size={14} /> {fetchingFile ? 'Saving...' : 'Save File'}
                      </button>
                    )}
                  </div>
                </div>

                <div className="download-progress-track">
                  <div
                    className="download-progress-fill"
                    style={{
                      width: `${currentDl.progress || 0}%`,
                      background:
                        currentDl.status === 'error' ? 'var(--accent-rose)' :
                        currentDl.status === 'completed' ? 'var(--accent-green)' :
                        'var(--gradient-primary)',
                    }}
                  />
                </div>

                <div className="download-stats">
                  <span>{currentDl.downloaded_chunks} / {currentDl.total_chunks} chunks</span>
                  <span>{formatBytes(currentDl.total_size)}</span>
                  <span className="download-progress-pct">{currentDl.progress}%</span>
                </div>

                {currentDl.error_message && (
                  <div className="download-error">{currentDl.error_message}</div>
                )}
              </div>
            </div>
          )}

          {success && <div className="alert alert-success">Downloaded successfully.</div>}
          {error && <div className="alert alert-error">{error}</div>}
        </div>
      </Card>

      {/* Phase 21: All active downloads */}
      <ActiveDownloads />

      {/* Phase 20: Preview Modal */}
      <PreviewModal
        isOpen={showPreview}
        onClose={() => setShowPreview(false)}
        fileHash={hash}
        filename={previewFilename}
        password={password}
      />
    </>
  )
}
