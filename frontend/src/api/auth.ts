import type { AuthTokens, LoginRequest, RegisterRequest, User } from '../types'

const AUTH_KEY = 'travel_planner_auth'

function storeAuth(tokens: AuthTokens, user: User) {
  localStorage.setItem(AUTH_KEY, JSON.stringify({ tokens, user }))
}

function getStoredAuth(): { tokens: AuthTokens; user: User } | null {
  const raw = localStorage.getItem(AUTH_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw)
  } catch {
    return null
  }
}

export async function login(req: LoginRequest): Promise<{ tokens: AuthTokens; user: User }> {
  // Simulate auth - backend uses user_id field, no real auth endpoints
  // In a real app, this would call POST /api/v1/auth/login
  const user: User = {
    id: `user_${Date.now()}`,
    username: req.username,
    email: `${req.username}@example.com`,
  }
  const tokens: AuthTokens = {
    accessToken: `token_${Date.now()}`,
    refreshToken: `refresh_${Date.now()}`,
  }
  storeAuth(tokens, user)
  return { tokens, user }
}

export async function register(req: RegisterRequest): Promise<{ tokens: AuthTokens; user: User }> {
  const user: User = {
    id: `user_${Date.now()}`,
    username: req.username,
    email: req.email,
  }
  const tokens: AuthTokens = {
    accessToken: `token_${Date.now()}`,
    refreshToken: `refresh_${Date.now()}`,
  }
  storeAuth(tokens, user)
  return { tokens, user }
}

export function logout() {
  localStorage.removeItem(AUTH_KEY)
}

export function getStoredUser(): User | null {
  const auth = getStoredAuth()
  return auth?.user ?? null
}

export function getStoredToken(): string | null {
  const auth = getStoredAuth()
  return auth?.tokens.accessToken ?? null
}
