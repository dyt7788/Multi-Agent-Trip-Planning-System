import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { ChevronDown, ChevronUp, User, Bot, Brain } from 'lucide-react'
import type { Message, AgentTrace } from '../../types'

interface Props {
  message: Message
}

export default function MessageItem({ message }: Props) {
  const isUser = message.role === 'user'
  const [showTrace, setShowTrace] = useState(false)
  const [showReport, setShowReport] = useState(false)

  if (isUser) {
    return (
      <div className="message user-message">
        <div className="message-avatar user-avatar">
          <User size={18} />
        </div>
        <div className="message-content">
          <div className="message-text">{message.content}</div>
        </div>
      </div>
    )
  }

  const htmlReport = message.reports?.find(report => report.type === 'html')

  // Extract images from plan data
  const planImages: { url: string; label: string }[] = []
  if (message.plan) {
    // Get images from itinerary slots if available
    const itinerary = message.plan.itinerary ?? []
    for (const day of itinerary) {
      for (const slot of day.slots ?? []) {
        if ((slot as any).images?.length > 0) {
          for (const url of (slot as any).images.slice(0, 2)) {
            planImages.push({ url, label: slot.title })
          }
        }
      }
    }
  }

  return (
    <div className="message assistant-message">
      <div className="message-avatar assistant-avatar">
        <Bot size={18} />
      </div>
      <div className="message-content">
        {message.trace && message.trace.length > 0 && (
          <div className="trace-section">
            <button className="trace-toggle" onClick={() => setShowTrace(!showTrace)}>
              <Brain size={14} />
              <span>思考过程 ({message.trace.length} 步)</span>
              {showTrace ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
            {showTrace && (
              <div className="trace-content">
                {message.trace.map((t, i) => (
                  <TraceStep key={i} trace={t} index={i} />
                ))}
              </div>
            )}
          </div>
        )}
        {planImages.length > 0 && (
          <div className="image-gallery">
            {planImages.slice(0, 6).map((img, i) => (
              <div key={i} className="image-card">
                <img src={img.url} alt={img.label} loading="lazy" />
                <span className="image-label">{img.label}</span>
              </div>
            ))}
          </div>
        )}
        {htmlReport && (
          <div className="report-section">
            <div className="report-header">
              <button className="report-toggle" onClick={() => setShowReport(!showReport)}>
                <span>旅行报告</span>
                {showReport ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              </button>
              <a className="report-link" href={htmlReport.url} target="_blank" rel="noreferrer">
                新窗口打开
              </a>
            </div>
            {showReport && (
              <iframe
                className="report-frame"
                src={htmlReport.url}
                title="旅行报告"
                sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
              />
            )}
          </div>
        )}
        <div className="message-text markdown">
          <ReactMarkdown>{message.content}</ReactMarkdown>
        </div>
      </div>
    </div>
  )
}

function TraceStep({ trace, index }: { trace: AgentTrace; index: number }) {
  const statusColors: Record<string, string> = {
    completed: 'var(--success)',
    started: 'var(--warning)',
    failed: 'var(--error)',
    skipped: 'var(--text-muted)',
  }

  return (
    <div className="trace-step">
      <div className="trace-step-header">
        <span className="trace-index">{index + 1}</span>
        <span className="trace-agent">{trace.agent}</span>
        <span
          className="trace-status"
          style={{ color: statusColors[trace.status] ?? 'var(--text-secondary)' }}
        >
          {trace.status}
        </span>
      </div>
      {trace.message && <div className="trace-step-body">{trace.message}</div>}
    </div>
  )
}
