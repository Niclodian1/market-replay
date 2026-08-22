'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'

export default function LoginPage() {
  const router = useRouter()
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError('')

    try {
      // 单用户模式：后端验证密码
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/auth/login`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password }),
        }
      )

      if (res.ok) {
        const data = await res.json()
        // 存储简单 token（阶段1先用 sessionStorage，后续完善）
        sessionStorage.setItem('auth_token', data.token || 'authenticated')
        router.push('/')
      } else if (res.status === 503) {
        setError('后端未配置 APP_PASSWORD，无法登录')
      } else {
        setError('密码错误，请重试')
      }
    } catch {
      setError('无法连接到服务器')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center">
      <div className="bg-gray-900 rounded-lg p-8 w-full max-w-sm border border-gray-800 shadow-xl">
        <div className="mb-6 text-center">
          <h1 className="text-2xl font-bold text-gray-100">行情复盘助手</h1>
          <p className="text-gray-500 text-sm mt-1">Market Replay Assistant</p>
        </div>

        <form onSubmit={handleLogin} className="space-y-4">
          <div>
            <label className="block text-sm text-gray-400 mb-1">访问密码</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 placeholder-gray-600 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-colors"
              placeholder="请输入密码"
              autoFocus
              required
            />
          </div>

          {error && (
            <div className="text-red-400 text-sm bg-red-900/20 border border-red-800 rounded px-3 py-2">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading || !password}
            className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-md px-4 py-2 font-medium transition-colors"
          >
            {loading ? '验证中...' : '进入'}
          </button>
        </form>

        <p className="text-center text-gray-600 text-xs mt-6">
          单用户私有工具 · 仅供个人使用
        </p>
      </div>
    </div>
  )
}
