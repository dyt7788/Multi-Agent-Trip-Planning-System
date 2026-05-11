import { MessageSquare, Plus, LogOut, Trash2, Loader2 } from 'lucide-react'
import type { Conversation } from '../../types'

interface Props {
  conversations: Conversation[]
  activeId: string | null
  onSelect: (id: string) => void
  onNew: () => void
  onDelete: (id: string) => void
  onLogout: () => void
  username: string
  loading?: boolean
}

export default function Sidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
  onLogout,
  username,
  loading,
}: Props) {
  return (
    <div className="sidebar">
      <div className="sidebar-header">
        <button className="new-chat-btn" onClick={onNew}>
          <Plus size={18} />
          <span>新对话</span>
        </button>
      </div>
      <div className="sidebar-list">
        {loading && (
          <div className="sidebar-empty">
            <Loader2 size={24} className="spin" />
            <span>加载中...</span>
          </div>
        )}
        {!loading && conversations.length === 0 && (
          <div className="sidebar-empty">
            <MessageSquare size={24} />
            <span>暂无对话记录</span>
          </div>
        )}
        {conversations.map(conv => (
          <div
            key={conv.id}
            className={`sidebar-item ${conv.id === activeId ? 'active' : ''}`}
            onClick={() => onSelect(conv.id)}
          >
            <MessageSquare size={16} className="sidebar-item-icon" />
            <span className="sidebar-item-title">{conv.title}</span>
            <button
              className="sidebar-item-delete"
              onClick={e => {
                e.stopPropagation()
                onDelete(conv.id)
              }}
              title="删除"
            >
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </div>
      <div className="sidebar-footer">
        <div className="sidebar-user">
          <div className="sidebar-avatar">{username[0]?.toUpperCase()}</div>
          <span className="sidebar-username">{username}</span>
        </div>
        <button className="logout-btn" onClick={onLogout}>
          <LogOut size={16} />
          <span>退出</span>
        </button>
      </div>
    </div>
  )
}
