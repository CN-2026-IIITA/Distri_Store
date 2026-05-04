/**
 * TransferSpeedChart — Real-time line chart showing upload/download throughput.
 * Reads from Zustand global store — no props needed.
 */

import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import { Activity } from 'lucide-react'
import useNetworkStore from '../../store/useNetworkStore'
import Card from '../ui/Card'

function formatTime(ts) {
  const d = new Date(ts)
  return d.toLocaleTimeString([], { minute: '2-digit', second: '2-digit' })
}

export default function TransferSpeedChart() {
  const throughputHistory = useNetworkStore((s) => s.throughputHistory)

  const data = throughputHistory.map((d) => ({
    time: formatTime(d.time),
    Download: d.download.toFixed(1),
    Upload: d.upload.toFixed(1),
  }))

  return (
    <Card title="Transfer Speed" icon={<Activity size={18} />}>
      <div style={{ width: '100%', height: 250 }}>
        <ResponsiveContainer>
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(15,23,42,0.06)" />
            <XAxis
              dataKey="time"
              stroke="rgba(15,23,42,0.15)"
              tick={{ fontSize: 11, fill: 'rgba(90,100,120,0.85)' }}
              interval="preserveStartEnd"
            />
            <YAxis
              stroke="rgba(15,23,42,0.15)"
              tick={{ fontSize: 11, fill: 'rgba(90,100,120,0.85)' }}
              label={{ value: 'MB/s', angle: -90, position: 'insideLeft', style: { fill: 'rgba(90,100,120,0.7)', fontSize: 11 } }}
            />
            <Tooltip
              contentStyle={{
                background: '#ffffff',
                border: '1px solid #eaecef',
                borderRadius: 10,
                color: '#1a1f36',
                fontSize: 12,
                boxShadow: '0 4px 12px rgba(15,23,42,0.06)',
              }}
            />
            <Legend wrapperStyle={{ fontSize: 12, color: '#5a6478' }} />
            <Line
              type="monotone"
              dataKey="Download"
              stroke="#6366f1"
              strokeWidth={2.25}
              dot={false}
              animationDuration={300}
            />
            <Line
              type="monotone"
              dataKey="Upload"
              stroke="#10b981"
              strokeWidth={2.25}
              dot={false}
              animationDuration={300}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </Card>
  )
}
