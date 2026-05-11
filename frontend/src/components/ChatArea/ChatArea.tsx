import { useState, useCallback, useRef } from 'react'
import { Send, Loader2 } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import { streamPlanTrip, streamModifyTrip, saveConversation } from '../../api/client'
import type { StreamEvent, StreamDoneData } from '../../api/client'
import type { Conversation, Message, AgentTrace, ItineraryPlan } from '../../types'
import MessageList from './MessageList'
import './chat.css'

interface Props {
  conversation: Conversation | null
  onUpdate: (id: string, updates: Partial<Conversation>) => void
}

let msgId = Date.now()

function isModifyIntent(text: string): boolean {
  return /修改|调整|换|去掉|删除|增加|添加|换成|改成|改/.test(text)
}

function findLastPlan(messages: Message[]): ItineraryPlan | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === 'assistant' && messages[i].plan) {
      return messages[i].plan as unknown as ItineraryPlan
    }
  }
  return null
}

function buildResponseText(data: StreamDoneData): string {
  const parts: string[] = []
  const plan = data.plan
  if (plan) {
    parts.push(`## ${plan.destination} ${plan.days}天旅行规划`)
    parts.push('')
    if (plan.summary) parts.push(String(plan.summary))
    parts.push('')
    const highlights = (plan.highlights as string[]) ?? []
    if (highlights.length > 0) {
      parts.push('### 亮点推荐')
      highlights.forEach(h => parts.push(`- ${h}`))
      parts.push('')
    }
    const itinerary = (plan.itinerary as Record<string, unknown>[]) ?? []
    if (itinerary.length > 0) {
      parts.push('### 行程安排')
      itinerary.forEach(day => {
        parts.push(`**第${day.day}天 - ${day.theme}**`)
        const slots = (day.slots as Record<string, unknown>[]) ?? []
        slots.forEach(slot => {
          parts.push(`- ${slot.time} **${slot.title}** - ${slot.description}`)
        })
        parts.push('')
      })
    }
    const restaurants = (plan.restaurants as string[]) ?? []
    if (restaurants.length > 0) {
      parts.push('### 推荐餐厅')
      restaurants.forEach(r => parts.push(`- ${r}`))
      parts.push('')
    }
    const packingTips = (plan.packing_tips as string[]) ?? []
    if (packingTips.length > 0) {
      parts.push('### 打包提示')
      packingTips.forEach(t => parts.push(`- ${t}`))
      parts.push('')
    }
    const detailed = plan.detailed_plan as Record<string, unknown> | undefined
    if (detailed) {
      const weatherInfo = (detailed.weather_info as Record<string, unknown>[]) ?? []
      if (weatherInfo.length > 0) {
        parts.push('### 天气与住宿')
        weatherInfo.forEach((w, index) => {
          const date = w.date ? `${w.date} ` : ''
          const dayTemp = w.day_temp ?? '待查'
          const nightTemp = w.night_temp ?? '待查'
          parts.push(`- 第${index + 1}天 ${date}${w.day_weather}/${w.night_weather}，${dayTemp}-${nightTemp}℃`)
        })
      }
      const detailedDays = (detailed.days as Record<string, unknown>[]) ?? []
      detailedDays.forEach((d, index) => {
        const hotel = d.hotel as Record<string, unknown> | undefined
        if (hotel?.name) {
          parts.push(`- 第${index + 1}天住宿：${hotel.name}，${hotel.price_range || hotel.estimated_cost || '价格待查'}`)
        }
      })
      const budget = detailed.budget as Record<string, unknown> | undefined
      if (budget?.total) {
        parts.push(`- 预算合计：${budget.total} 元`)
      }
      parts.push('')
    }
  }
  return parts.join('\n') || '旅行规划已生成。'
}

export default function ChatArea({ conversation, onUpdate }: Props) {
  const { user, token } = useAuth()
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [streamTraces, setStreamTraces] = useState<AgentTrace[]>([])
  const [streamThinking, setStreamThinking] = useState<Record<string, string>>({})
  const abortRef = useRef<AbortController | null>(null)

  const persistConversation = useCallback(async (convId: string, title: string, messages: Message[], destination: string, days: number) => {
    try {
      await saveConversation(
        convId,
        title,
        messages.map(m => ({
          id: m.id,
          role: m.role,
          content: m.content,
          trace: m.trace,
          plan: m.plan ? JSON.parse(JSON.stringify(m.plan)) : undefined,
          reports: m.reports ? JSON.parse(JSON.stringify(m.reports)) : undefined,
          timestamp: m.timestamp,
        })),
        destination,
        days,
        token,
      )
    } catch {
      // Silently fail, backend might not have endpoint yet
    }
  }, [token])

  const handleSend = useCallback(async () => {
    if (!input.trim() || !conversation || loading) return

    const userMsg: Message = {
      id: `msg_${++msgId}`,
      role: 'user',
      content: input.trim(),
      timestamp: new Date().toISOString(),
    }

    const updatedMessages = [...conversation.messages, userMsg]
    onUpdate(conversation.id, { messages: updatedMessages })

    const userInput = input.trim()
    setInput('')
    setLoading(true)
    setStreamTraces([])

    if (conversation.messages.length === 0) {
      const title = userInput.slice(0, 20) + (userInput.length > 20 ? '...' : '')
      onUpdate(conversation.id, { title })
    }

    // Check if this is a modify request
    const previousPlan = findLastPlan(conversation.messages)
    const isModify = previousPlan && isModifyIntent(userInput)

    // Send raw free_text; backend LLM will extract destination/days
    const baseReq = {
      destination: '',
      days: 0,
      preferences: [],
      preference_details: {},
      user_id: user?.id ?? 'guest',
      free_text: userInput,
      image_urls: [],
      xhs_post_urls: [],
      language: 'zh-CN',
      export_pdf: false,
    }

    let req: Record<string, unknown>
    let streamFn: typeof streamPlanTrip

    if (isModify) {
      req = {
        ...baseReq,
        mode: '修改报告',
        previous_plan: previousPlan,
        modification_request: userInput,
      }
      streamFn = streamModifyTrip
    } else {
      req = { ...baseReq, mode: '初次规划' }
      streamFn = streamPlanTrip
    }

    const traces: AgentTrace[] = []
    let finalData: StreamDoneData | null = null

    const controller = streamFn(
      req,
      token,
      (event: StreamEvent) => {
        if (event.type === 'agent_started') {
          const existing = traces.find(t => t.agent === event.data.agent && t.status === 'started')
          if (!existing) {
            traces.push({
              agent: event.data.agent,
              status: 'started',
              message: event.data.message,
              metadata: {},
            })
            setStreamTraces([...traces])
          }
        } else if (event.type === 'agent_completed') {
          const idx = traces.findIndex(t => t.agent === event.data.agent && t.status === 'started')
          if (idx >= 0) {
            traces[idx] = {
              agent: event.data.agent,
              status: 'completed',
              message: event.data.message,
              metadata: event.data.metadata ?? {},
            }
          } else {
            traces.push({
              agent: event.data.agent,
              status: 'completed',
              message: event.data.message,
              metadata: event.data.metadata ?? {},
            })
          }
          setStreamTraces([...traces])
        } else if (event.type === 'agent_thinking') {
          const agent = event.data.agent
          setStreamThinking(prev => ({
            ...prev,
            [agent]: (prev[agent] || '') + event.data.token,
          }))
        } else if (event.type === 'done') {
          finalData = event.data
        }
      },
      (err) => {
        console.error('Stream error:', err)
        const errMsg: Message = {
          id: `msg_${++msgId}`,
          role: 'assistant',
          content: `抱歉，请求失败：${err.message}`,
          timestamp: new Date().toISOString(),
        }
        onUpdate(conversation.id, {
          messages: [...updatedMessages, errMsg],
        })
        setLoading(false)
        setStreamTraces([])
        setStreamThinking({})
      },
      () => {
        setLoading(false)
        setStreamTraces([])
        setStreamThinking({})

        if (finalData) {
          const dest = (finalData!.plan.destination as string) || userInput
          const dayCount = (finalData!.plan.days as number) || 3
          const assistantMsg: Message = {
            id: `msg_${++msgId}`,
            role: 'assistant',
            content: buildResponseText(finalData!),
            trace: finalData!.trace as AgentTrace[],
            plan: finalData!.plan as unknown as ItineraryPlan,
            reports: finalData!.reports,
            timestamp: new Date().toISOString(),
          }
          const finalMessages = [...updatedMessages, assistantMsg]
          onUpdate(conversation.id, {
            messages: finalMessages,
            destination: dest,
            days: dayCount,
          })
          // Persist to backend
          persistConversation(conversation.id, conversation.title || userInput.slice(0, 20), finalMessages, dest, dayCount)
        }
      },
    )

    abortRef.current = controller
  }, [input, conversation, loading, user, token, onUpdate, persistConversation])

  const handleCancel = useCallback(() => {
    abortRef.current?.abort()
    setLoading(false)
    setStreamTraces([])
    setStreamThinking({})
  }, [])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="chat-area">
      {!conversation ? (
        <div className="chat-empty">
          <div className="chat-empty-icon">
            <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="1.5">
              <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z" />
              <circle cx="12" cy="10" r="3" />
            </svg>
          </div>
          <h2>开始你的旅行规划</h2>
          <p>告诉我你想去哪里，几天，有什么偏好</p>
          <p className="chat-empty-hint">例如："我想去成都玩3天，喜欢美食和文化"</p>
        </div>
      ) : (
        <>
          <MessageList
            messages={conversation.messages}
            loading={loading}
            traces={streamTraces}
            thinking={streamThinking}
          />
          <div className="chat-input-area">
            <textarea
              className="chat-input"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="输入你的旅行需求..."
              rows={1}
              disabled={loading}
            />
            {loading ? (
              <button className="chat-cancel-btn" onClick={handleCancel}>
                取消
              </button>
            ) : (
              <button
                className="chat-send-btn"
                onClick={handleSend}
                disabled={!input.trim()}
              >
                <Send size={20} />
              </button>
            )}
          </div>
        </>
      )}
    </div>
  )
}
