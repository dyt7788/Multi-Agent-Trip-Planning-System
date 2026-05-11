import { useEffect, useRef } from 'react'
import type { Message, AgentTrace } from '../../types'
import MessageItem from './MessageItem'
import './chat.css'

interface Props {
  messages: Message[]
  loading: boolean
  traces: AgentTrace[]
  thinking?: Record<string, string>
}

export default function MessageList({ messages, loading, traces, thinking }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight
    }
  }, [messages, traces])

  return (
    <div className="message-list" ref={containerRef}>
      {messages.length === 0 && !loading && (
        <div className="message-list-empty">
          <span>发送消息开始对话</span>
        </div>
      )}
      {messages.map(msg => (
        <MessageItem key={msg.id} message={msg} />
      ))}
      {loading && (
        <div className="streaming-thinking">
          <div className="streaming-header">
            <span className="streaming-icon" />
            <span className="streaming-title">AI 思考中</span>
          </div>
          <div className="streaming-agents">
            {traces.length === 0 && (
              <div className="streaming-waiting">
                <div className="streaming-dots">
                  <div className="streaming-dot" />
                  <div className="streaming-dot" />
                  <div className="streaming-dot" />
                </div>
                <span>正在规划你的旅行方案</span>
              </div>
            )}
            {traces.map((t, i) => (
              <div key={i} className={`streaming-step ${t.status}`}>
                <span className="streaming-step-index">{i + 1}</span>
                <span className="streaming-step-agent">{t.agent}</span>
                {t.status === 'started' && (
                  <span className="streaming-step-status">
                    <span className="spinner-mini" /> 进行中
                  </span>
                )}
                {t.status === 'completed' && (
                  <span className="streaming-step-status done">完成</span>
                )}
              </div>
            ))}
            {Object.entries(thinking ?? {}).map(([agent, text]) => {
              const isCurrentAgent = traces.some(t => t.agent === agent && t.status === 'started')
              if (!isCurrentAgent) return null
              return (
                <div key={agent} className="streaming-thinking-content">
                  <span className="streaming-thinking-text">{text}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
