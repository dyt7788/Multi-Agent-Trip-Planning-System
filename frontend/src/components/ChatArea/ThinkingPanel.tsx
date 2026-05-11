import { Brain } from 'lucide-react'
import type { AgentTrace } from '../../types'

interface Props {
  traces: AgentTrace[]
}

export default function ThinkingPanel({ traces }: Props) {
  if (traces.length === 0) return null

  const statusColors: Record<string, string> = {
    completed: 'var(--success)',
    started: 'var(--warning)',
    failed: 'var(--error)',
    skipped: 'var(--text-muted)',
  }

  return (
    <div className="thinking-panel active">
      <div className="thinking-header">
        <Brain size={16} />
        <span className="thinking-title">Agent 思考过程</span>
      </div>
      <div className="thinking-body">
        {traces.map((t, i) => (
          <div key={i} className="thinking-step">
            <span
              className="thinking-step-dot"
              style={{ background: statusColors[t.status] ?? 'var(--text-secondary)' }}
            />
            <span className="thinking-step-agent">{t.agent}</span>
            {t.message && <span className="thinking-step-msg">{t.message}</span>}
          </div>
        ))}
      </div>
    </div>
  )
}
