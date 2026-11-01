import { QueryClient } from '@tanstack/react-query'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30 * 1000,       // 30秒内不重新请求
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: 0,
    },
  },
})
