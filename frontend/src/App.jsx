/**
 * DistriStore — App.jsx (Enterprise Architecture)
 *
 * This file ONLY handles:
 *   1. BrowserRouter + Route mapping
 *   2. Global layout (Header + Sidebar + content area)
 *   3. Zustand store polling initialization
 *
 * All business logic lives in pages/ and components/.
 */

import { useEffect } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import './index.css'

import useNetworkStore from './store/useNetworkStore'
import Header from './components/layout/Header'
import Sidebar from './components/layout/Sidebar'

import DashboardPage from './pages/DashboardPage'
import UploadPage from './pages/UploadPage'
import DownloadPage from './pages/DownloadPage'
import SettingsPage from './pages/SettingsPage'
import ChatsPage from './pages/ChatsPage'
import SharedWithMePage from './pages/SharedWithMePage'
import ChatDrawer from './components/network/ChatDrawer'

function AppShell() {
  const startPolling = useNetworkStore((s) => s.startPolling)
  const stopPolling = useNetworkStore((s) => s.stopPolling)

  useEffect(() => {
    startPolling(3000)
    return () => stopPolling()
  }, [startPolling, stopPolling])

  return (
    <div className="app-shell">
      <Header />
      <div className="app-body">
        <Sidebar />
        <main className="app-content">
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/upload" element={<UploadPage />} />
            <Route path="/download" element={<DownloadPage />} />
            <Route path="/chats" element={<ChatsPage />} />
            <Route path="/shared" element={<SharedWithMePage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </main>
      </div>
      <ChatDrawer />
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AppShell />
    </BrowserRouter>
  )
}
