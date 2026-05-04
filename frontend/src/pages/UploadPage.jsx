/**
 * UploadPage — File upload with drag & drop, encryption, and result display.
 */

import { useState } from 'react'
import { Upload, Lock, FileUp, CheckCircle2 } from 'lucide-react'
import { uploadFile } from '../api/client'
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

  const handleUpload = async () => {
    if (!file) return
    setUploading(true); setError(null); setResult(null)
    try {
      const data = await uploadFile(file, password)
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

        {/* Password */}
        <div className="input-group">
          <label><Lock size={14} /> Encryption Password (optional)</label>
          <input type="password" className="input-field" placeholder="Leave empty for no encryption" value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>

        {/* Upload Button */}
        <Button variant="primary" loading={uploading} disabled={!file} onClick={handleUpload}>
          Upload file
        </Button>

        {/* Success */}
        {result && (
          <div className="alert alert-success">
            <div style={{ flex: 1 }}>
              <div>Upload complete. {result.chunks} chunks · Merkle: {result.manifest?.merkle_root?.slice(0, 16)}...</div>
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
