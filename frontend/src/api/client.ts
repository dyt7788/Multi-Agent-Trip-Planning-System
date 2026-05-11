const API_BASE = '/api/v1'

export type StreamEvent =
  | { type: 'agent_started'; data: { agent: string; message: string } }
  | { type: 'agent_completed'; data: { agent: string; message: string; metadata?: Record<string, unknown> } }
  | { type: 'agent_thinking'; data: { agent: string; token: string } }
  | { type: 'done'; data: StreamDoneData }
  | { type: 'error'; data: { error: string } }

export interface StreamDoneData {
  plan: Record<string, unknown>
  query: {
    destination: string
    sources: unknown[]
    extracted: {
      destination: string
      summary: string
      attractions: string[]
      restaurants: string[]
      activities: string[]
      route_suggestions: string[]
      tips: string[]
    }
  }
  images: {
    destination: string | null
    observations: {
      image_url: string
      labels: string[]
      scene_type: string
      inferred_location: string | null
      description: string
    }[]
  }
  trace: unknown[]
  reports: {
    type: 'html' | 'pdf' | 'json'
    path: string
    url: string
    generated_at: string
  }[]
}

function getHeaders(token?: string): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`
  return headers
}

export function streamPlanTrip(
  request: Record<string, unknown>,
  token: string | null,
  onEvent: (event: StreamEvent) => void,
  onError: (err: Error) => void,
  onDone: () => void,
): AbortController {
  const controller = new AbortController()

  fetch(`${API_BASE}/trips/plan/stream`, {
    method: 'POST',
    headers: getHeaders(token ?? undefined),
    body: JSON.stringify(request),
    signal: controller.signal,
  })
    .then(async (res) => {
      if (!res.ok) {
        onError(new Error(`HTTP ${res.status}: ${res.statusText}`))
        return
      }
      const reader = res.body?.getReader()
      if (!reader) {
        onError(new Error('No response body'))
        return
      }
      const decoder = new TextDecoder()
      let buffer = ''

      let currentEvent = 'message'
      let currentData = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim()
            currentData = ''
          } else if (line.startsWith('data: ')) {
            currentData = line.slice(6).trim()
          } else if (line === '') {
            // End of SSE event, dispatch
            if (currentData) {
              try {
                const data = JSON.parse(currentData)
                onEvent({ type: currentEvent as StreamEvent['type'], data })
              } catch {
                // Skip malformed JSON
              }
            }
            currentEvent = 'message'
            currentData = ''
          }
        }
      }
      // Flush remaining buffer
      if (buffer.trim()) {
        const lines = buffer.split('\n')
        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim()
            currentData = ''
          } else if (line.startsWith('data: ')) {
            currentData = line.slice(6).trim()
          } else if (line === '') {
            if (currentData) {
              try {
                const data = JSON.parse(currentData)
                onEvent({ type: currentEvent as StreamEvent['type'], data })
              } catch {
                // Skip malformed JSON
              }
            }
            currentEvent = 'message'
            currentData = ''
          }
        }
      }
      onDone()
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        onError(err)
      }
    })

  return controller
}

export async function planTrip(
  request: Record<string, unknown>,
  token?: string,
): Promise<Response> {
  return fetch(`${API_BASE}/trips/plan`, {
    method: 'POST',
    headers: getHeaders(token),
    body: JSON.stringify(request),
  })
}

export async function getPreferences(userId: string, token?: string) {
  const res = await fetch(`${API_BASE}/users/${userId}/preferences`, {
    headers: getHeaders(token),
  })
  if (!res.ok) throw new Error('Failed to get preferences')
  return res.json()
}

export async function updatePreferences(
  userId: string,
  preferences: Record<string, unknown>,
  token?: string,
) {
  const res = await fetch(`${API_BASE}/users/${userId}/preferences`, {
    method: 'PUT',
    headers: getHeaders(token),
    body: JSON.stringify(preferences),
  })
  if (!res.ok) throw new Error('Failed to update preferences')
  return res.json()
}

export async function getReport(tripId: string, format: 'html' | 'pdf') {
  window.open(`${API_BASE}/reports/${tripId}.${format}`, '_blank')
}

// Stream modify trip
export function streamModifyTrip(
  request: Record<string, unknown>,
  token: string | null,
  onEvent: (event: StreamEvent) => void,
  onError: (err: Error) => void,
  onDone: () => void,
): AbortController {
  const controller = new AbortController()

  fetch(`${API_BASE}/trips/modify/stream`, {
    method: 'POST',
    headers: getHeaders(token ?? undefined),
    body: JSON.stringify(request),
    signal: controller.signal,
  })
    .then(async (res) => {
      if (!res.ok) {
        onError(new Error(`HTTP ${res.status}: ${res.statusText}`))
        return
      }
      const reader = res.body?.getReader()
      if (!reader) {
        onError(new Error('No response body'))
        return
      }
      const decoder = new TextDecoder()
      let buffer = ''

      let currentEvent = 'message'
      let currentData = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim()
            currentData = ''
          } else if (line.startsWith('data: ')) {
            currentData = line.slice(6).trim()
          } else if (line === '') {
            // End of SSE event, dispatch
            if (currentData) {
              try {
                const data = JSON.parse(currentData)
                onEvent({ type: currentEvent as StreamEvent['type'], data })
              } catch {
                // Skip malformed JSON
              }
            }
            currentEvent = 'message'
            currentData = ''
          }
        }
      }
      // Flush remaining buffer
      if (buffer.trim()) {
        const lines = buffer.split('\n')
        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim()
            currentData = ''
          } else if (line.startsWith('data: ')) {
            currentData = line.slice(6).trim()
          } else if (line === '') {
            if (currentData) {
              try {
                const data = JSON.parse(currentData)
                onEvent({ type: currentEvent as StreamEvent['type'], data })
              } catch {
                // Skip malformed JSON
              }
            }
            currentEvent = 'message'
            currentData = ''
          }
        }
      }
      onDone()
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        onError(err)
      }
    })

  return controller
}

// ===== Conversation CRUD =====

export interface SavedConversation {
  id: string
  title: string
  destination?: string
  days?: number
  created_at: string
  updated_at: string
}

export interface ConversationDetail {
  id: string
  title: string
  messages: Record<string, unknown>[]
  destination: string
  days: number
  created_at: string
  updated_at: string
}

export async function saveConversation(
  convId: string,
  title: string,
  messages: Record<string, unknown>[],
  destination: string,
  days: number,
  token: string | null,
): Promise<void> {
  const res = await fetch(`${API_BASE}/conversations`, {
    method: 'POST',
    headers: getHeaders(token ?? undefined),
    body: JSON.stringify({ id: convId, title, messages, destination, days }),
  })
  if (!res.ok) throw new Error('Failed to save conversation')
}

export async function loadConversations(
  userId: string,
  token: string | null,
): Promise<SavedConversation[]> {
  const res = await fetch(`${API_BASE}/users/${userId}/conversations`, {
    headers: getHeaders(token ?? undefined),
  })
  if (!res.ok) throw new Error('Failed to load conversations')
  return res.json()
}

export async function loadConversation(
  convId: string,
  token: string | null,
): Promise<ConversationDetail> {
  const res = await fetch(`${API_BASE}/conversations/${convId}`, {
    headers: getHeaders(token ?? undefined),
  })
  if (!res.ok) throw new Error('Failed to load conversation')
  return res.json()
}

export async function deleteConversation(
  convId: string,
  token: string | null,
): Promise<void> {
  const res = await fetch(`${API_BASE}/conversations/${convId}`, {
    method: 'DELETE',
    headers: getHeaders(token ?? undefined),
  })
  if (!res.ok) throw new Error('Failed to delete conversation')
}
