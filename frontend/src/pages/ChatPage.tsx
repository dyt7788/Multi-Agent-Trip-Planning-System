import { useState, useCallback, useEffect } from 'react'
import { useAuth } from '../contexts/AuthContext'
import type { Conversation, Message } from '../types'
import { loadConversations, loadConversation, deleteConversation as apiDelete } from '../api/client'
import Sidebar from '../components/Sidebar'
import ChatArea from '../components/ChatArea'

let idCounter = Date.now()
function newId() {
  return `conv_${++idCounter}`
}

function messagesFromRaw(raw: Record<string, unknown>[]): Message[] {
  return raw.map(m => ({
    id: String(m.id || `msg_${Math.random().toString(36).slice(2)}`),
    role: (m.role as 'user' | 'assistant') || 'user',
    content: String(m.content || ''),
    trace: (m.trace as Message['trace']) ?? undefined,
    plan: (m.plan as Message['plan']) ?? undefined,
    reports: (m.reports as Message['reports']) ?? undefined,
    timestamp: String(m.timestamp || new Date().toISOString()),
  }))
}

export default function ChatPage() {
  const { user, token, logout } = useAuth()
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [loadingHistory, setLoadingHistory] = useState(true)

  // Load conversation list on mount
  useEffect(() => {
    if (!user) return
    loadConversations(user.id, token)
      .then(rows => {
        const convs: Conversation[] = rows.map(r => ({
          id: r.id,
          title: r.title,
          userId: user.id,
          messages: [],
          createdAt: r.created_at,
          updatedAt: r.updated_at,
          destination: r.destination,
          days: r.days,
        }))
        setConversations(convs)
      })
      .catch(() => {
        // Backend might not have the endpoint yet, continue with empty
      })
      .finally(() => setLoadingHistory(false))
  }, [user, token])

  const activeConversation = conversations.find(c => c.id === activeId) ?? null

  const createNewChat = useCallback(() => {
    const conv: Conversation = {
      id: newId(),
      title: '新对话',
      userId: user!.id,
      messages: [],
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    }
    setConversations(prev => [conv, ...prev])
    setActiveId(conv.id)
  }, [user])

  const updateConversation = useCallback((id: string, updates: Partial<Conversation>) => {
    setConversations(prev =>
      prev.map(c => c.id === id ? { ...c, ...updates, updatedAt: new Date().toISOString() } : c),
    )
  }, [])

  const deleteConversation = useCallback((id: string) => {
    apiDelete(id, token).catch(() => {})
    setConversations(prev => prev.filter(c => c.id !== id))
    if (activeId === id) setActiveId(null)
  }, [activeId, token])

  const selectConversation = useCallback((id: string) => {
    setActiveId(id)
    // If messages not loaded, fetch from backend
    const existing = conversations.find(c => c.id === id)
    if (existing && existing.messages.length === 0) {
      loadConversation(id, token)
        .then(detail => {
          setConversations(prev =>
            prev.map(c =>
              c.id === id
                ? {
                    ...c,
                    messages: messagesFromRaw(detail.messages as Record<string, unknown>[]),
                    title: detail.title,
                    destination: detail.destination,
                    days: detail.days,
                  }
                : c,
            ),
          )
        })
        .catch(() => {})
    }
  }, [conversations, token])

  return (
    <div className="chat-page">
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        onSelect={selectConversation}
        onNew={createNewChat}
        onDelete={deleteConversation}
        onLogout={logout}
        username={user?.username ?? ''}
        loading={loadingHistory}
      />
      <ChatArea
        conversation={activeConversation}
        onUpdate={updateConversation}
      />
    </div>
  )
}
