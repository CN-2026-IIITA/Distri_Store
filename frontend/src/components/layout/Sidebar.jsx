/**
 * Sidebar — Navigation sidebar with route links and active indicators.
 */

import { NavLink } from 'react-router-dom'
import { LayoutDashboard, Upload, Download, MessageSquare, Settings } from 'lucide-react'
import clsx from 'clsx'

const navItems = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/upload', label: 'Upload', icon: Upload },
  { to: '/download', label: 'Download', icon: Download },
  { to: '/chats', label: 'Chats', icon: MessageSquare },
  { to: '/settings', label: 'Settings', icon: Settings },
]

export default function Sidebar() {
  return (
    <nav className="sidebar">
      {navItems.map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          className={({ isActive }) => clsx('sidebar-link', isActive && 'sidebar-link-active')}
        >
          <Icon size={18} />
          <span>{label}</span>
        </NavLink>
      ))}
    </nav>
  )
}
