import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react'
import type { User, AuthTokens } from '../types'
import { login as apiLogin, register as apiRegister, logout as apiLogout, getStoredUser, getStoredToken } from '../api/auth'

interface AuthState {
  user: User | null
  token: string | null
  isAuthenticated: boolean
  login: (username: string, password: string) => Promise<void>
  register: (username: string, email: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [token, setToken] = useState<string | null>(null)

  useEffect(() => {
    const storedUser = getStoredUser()
    const storedToken = getStoredToken()
    if (storedUser && storedToken) {
      setUser(storedUser)
      setToken(storedToken)
    }
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const { tokens, user: u } = await apiLogin({ username, password })
    setUser(u)
    setToken(tokens.accessToken)
  }, [])

  const registerFn = useCallback(async (username: string, email: string, password: string) => {
    const { tokens, user: u } = await apiRegister({ username, email, password })
    setUser(u)
    setToken(tokens.accessToken)
  }, [])

  const logout = useCallback(() => {
    apiLogout()
    setUser(null)
    setToken(null)
  }, [])

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        isAuthenticated: !!user,
        login,
        register: registerFn,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
