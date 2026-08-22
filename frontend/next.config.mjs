/** @type {import('next').NextConfig} */
const nextConfig = {
  async redirects() {
    return [
      { source: '/ma-strategies-pro', destination: '/ma-strategies', permanent: true },
      { source: '/ma-strategies-c', destination: '/ma-strategies', permanent: true },
    ]
  },
}

export default nextConfig
