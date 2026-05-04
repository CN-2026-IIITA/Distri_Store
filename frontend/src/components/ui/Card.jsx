/**
 * Card — Reusable panel component with icon, title, action slot, and glassmorphism styling.
 */

import clsx from 'clsx'

export default function Card({ children, title, icon, action, className, noPad }) {
  return (
    <div className={clsx('panel', className)}>
      {(title || icon || action) && (
        <div className="panel-title">
          <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {icon && <span className="icon">{icon}</span>}
            {title}
          </span>
          {action && <span className="panel-action">{action}</span>}
        </div>
      )}
      <div className={clsx(!noPad && 'card-body')}>
        {children}
      </div>
    </div>
  )
}
