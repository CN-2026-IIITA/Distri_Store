/**
 * RoutePath — Phase 25A: visualize the onion-routing path a chunk took.
 *
 * Renders a small horizontal chain of hops with their roles
 * (entry → relay → ... → holder). Each hop is a colored pill with
 * the peer name and an arrow connector between them.
 */

import { Server, Send, ShieldCheck, Inbox, ArrowRight } from 'lucide-react'

const ROLE_META = {
  entry:  { icon: Send,        bg: 'rgba(99,102,241,0.10)',  color: '#4f46e5', label: 'entry'  },
  relay:  { icon: Server,      bg: 'rgba(139,92,246,0.10)',  color: '#7c3aed', label: 'relay'  },
  holder: { icon: ShieldCheck, bg: 'rgba(16,185,129,0.10)',  color: '#047857', label: 'holder' },
  self:   { icon: Inbox,       bg: 'rgba(15,23,42,0.06)',    color: '#475569', label: 'you'    },
}

export default function RoutePath({ hops, selfNodeId, selfName, prefixSelf = true }) {
  if (!hops || hops.length === 0) {
    return (
      <div className="route-path-empty">
        no path recorded — file was served locally or no relays were available
      </div>
    )
  }

  // Optionally prepend a "you" node so the chain shows the requester
  const chain = []
  if (prefixSelf && selfNodeId) {
    chain.push({
      node_id: selfNodeId,
      name: (selfName || 'you') + ' (you)',
      role: 'self',
      online: true,
    })
  }
  hops.forEach((h) => chain.push(h))

  return (
    <div className="route-path">
      {chain.map((h, i) => {
        const meta = ROLE_META[h.role] || ROLE_META.relay
        const Icon = meta.icon
        return (
          <div className="route-hop-wrap" key={`${h.node_id}-${i}`}>
            <div
              className={`route-hop ${h.online ? '' : 'route-hop-offline'}`}
              style={{ background: meta.bg, color: meta.color, borderColor: meta.color + '33' }}
              title={`${h.role}: ${h.node_id}`}
            >
              <Icon size={14} />
              <span className="route-hop-name">{h.name || h.node_id.slice(0, 10) + '...'}</span>
              <span className="route-hop-role">{meta.label}</span>
            </div>
            {i < chain.length - 1 && <ArrowRight size={14} className="route-arrow" />}
          </div>
        )
      })}
    </div>
  )
}
